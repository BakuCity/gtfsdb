import sys
import time
import datetime
import logging
log = logging.getLogger(__name__)

from sqlalchemy import Column
from sqlalchemy.orm import deferred, relationship
from sqlalchemy.types import Integer, String, Date
from sqlalchemy.sql import func

from gtfsdb import config
from gtfsdb.model.base import Base

__all__ = ['RouteStop']


class RouteStop(Base):
    datasource = config.DATASOURCE_DERIVED

    __tablename__ = 'route_stops'

    route_id = Column(String(255), primary_key=True, index=True, nullable=False)
    direction_id = Column(Integer, primary_key=True, index=True, nullable=False)
    stop_id = Column(String(255), primary_key=True, index=True, nullable=False)
    order = Column(Integer, index=True, nullable=False)
    start_date = Column(Date, index=True, nullable=False)
    end_date = Column(Date, index=True, nullable=False)

    route = relationship(
        'Route',
        primaryjoin='RouteStop.route_id==Route.route_id',
        foreign_keys='(RouteStop.route_id)',
        uselist=False, viewonly=True, lazy='joined')

    stop = relationship(
        'Stop',
        primaryjoin='RouteStop.stop_id==Stop.stop_id',
        foreign_keys='(RouteStop.stop_id)',
        uselist=False, viewonly=True, lazy='joined')

    direction = relationship(
        'RouteDirection',
        primaryjoin='RouteStop.route_id==RouteDirection.route_id and RouteStop.direction_id==RouteDirection.direction_id',
        foreign_keys='(RouteStop.route_id, RouteStop.direction_id)',
        uselist=False, viewonly=True, lazy='joined')

    start_calendar = relationship(
        'UniversalCalendar',
        primaryjoin='RouteStop.start_date==UniversalCalendar.date',
        foreign_keys='(RouteStop.start_date)',
        uselist=True, viewonly=True)

    end_calendar = relationship(
        'UniversalCalendar',
        primaryjoin='RouteStop.end_date==UniversalCalendar.date',
        foreign_keys='(RouteStop.end_date)',
        uselist=True, viewonly=True)

    def is_active(self, date=None):
        """ :return False whenever we see that the route_stop's start and end date are
                    outside the input date (where the input date defaults to 'today')
        """
        _is_active = False
        if self.start_date and self.end_date:
            if date is None:
                date = datetime.date.today()
            if self.start_date <= date <= self.end_date:
                _is_active = True
        return _is_active

    @classmethod
    def active_stops(cls, session, route_id, direction_id, agency_id=None, date=None):
        ''' returns list of routes that are seen as 'active' based on dates and filters
        '''

        # step 1: default date
        if date is None or not isinstance(date, datetime.date):
            date = datetime.date.today()

        # step 2a: query all route stops
        q = session.query(RouteStop).filter(RouteStop.route_id == route_id).filter(RouteStop.direction_id == direction_id)

        # step 2b: filter based on date
        q = q.filter(RouteStop.start_date <= date).filter(date <= RouteStop.end_date)

        # step 2c: filter by any agency_id
        if agency_id:
            q = q.filter(RouteStop.agency_id == agency_id)

        # step 2d: add some stop order
        q = q.order_by(RouteStop.order)

        #import pdb; pdb.set_trace()
        route_stops = q.all()
        return route_stops

    @classmethod
    def load(cls, db, **kwargs):
        log.debug('{0}.load (loaded later in post_process)'.format(cls.__name__))
        pass

    @classmethod
    def post_process(cls, db):
        log.debug('{0}.post_process'.format(cls.__name__))
        cls.populate(db.session)

    @classmethod
    def populate(cls, session):
        ''' for each route/direction, find list of stop_ids for route/direction pairs

            the load is a two part process, where part A finds a list of unique stop ids, and
            part B creates the RouteStop (and potentially RouteDirections ... if not in GTFS) records
        '''
        from gtfsdb import Route, RouteDirection

        #import pdb; pdb.set_trace()
        start_time = time.time()
        routes = session.query(Route).all()

        for r in routes:
            # step 0: figure out some info about the route
            create_dirs = False
            if r.directions is None or len(r.directions) == 0:
                create_dirs = True

            # step 1: filter the list of trips down to only a trip with a unique pattern
            #   TODO: any way to have the orm do this?  Something probably really simple Mike?
            trips = []
            shape_id_filter = []
            for t in r.trips:
                # a bit of a speedup to filter trips that have the same shape
                if t.shape_id and t.shape_id in shape_id_filter:
                    continue
                # store our trips
                shape_id_filter.append(t.shape_id)
                trips.append(t)

            # step 2: sort our list of trips by length (note: for trips with two directions, ...)
            trips = sorted(trips, key=lambda t: t.trip_len, reverse=True)

            # PART A: we're going to just collect a list of unique stop ids for this route / directions 
            for d in [0, 1]:
                unique_stops = []

                # step 3: loop through all our trips and their stop times, pulling out a unique set of stops 
                for t in trips:
                    if t.direction_id == d:

                        # step 4: loop through this trip's stop times, and find any/all stops that are in our stop list already
                        #         further, let's try to find the best position of that stop (e.g., look for where the stop patterns breaks)
                        last_pos = None
                        for i, st in enumerate(t.stop_times):
                            # step 5a: make sure this stop that customers can actually board...
                            if st.is_boarding_stop():
                                if st.stop_id in unique_stops:
                                    last_pos = unique_stops.index(st.stop_id)
                                else:
                                    # step 5b: add ths stop id to our unique list ... either in position, or appended to the end of the list
                                    if last_pos:
                                        last_pos += 1
                                        unique_stops.insert(last_pos, st.stop_id)
                                    else:
                                        unique_stops.append(st.stop_id)

                # PART B: add records to the database ...
                if len(unique_stops) > 0:

                    # step 6: if an entry for the direction doesn't exist, create a new
                    #         RouteDirection record and add it to this route
                    if create_dirs:
                        rd = RouteDirection()
                        rd.route_id = r.route_id
                        rd.direction_id = d
                        rd.direction_name = "Outbound" if d is 0 else "Inbound"
                        session.add(rd)

                    # step 7: create new RouteStop records
                    for k, stop_id in enumerate(unique_stops):
                        # step 4b: create a RouteStop record
                        rs = RouteStop()
                        rs.route_id = r.route_id
                        rs.direction_id = d
                        rs.stop_id = stop_id
                        rs.order = k + 1
                        rs.start_date = r.start_date
                        rs.end_date =  r.end_date
                        session.add(rs)

            # step 8: commit the new records to the db for this route...
            sys.stdout.write('*')
            session.commit()

        # step 9: commit
        session.commit()
        session.flush()

        # step 10: now let's go thru and
        cls._fix_dates(session)
        session.close()

        processing_time = time.time() - start_time
        log.debug('{0}.post_process ({1:.0f} seconds)'.format(cls.__name__, processing_time))

    @classmethod
    def _fix_dates(cls, session):
        ''' fix up the route stop

            SELECT r.route_id, st.stop_id, min(date), max(date)
            FROM universal_calendar u, trips t, stop_times st, routes r
            where u.service_id = t.service_id
            and t.trip_id = st.trip_id
            and t.route_id = r.route_id
            group by r.route_id, st.stop_id
            order by 2



        '''
        return

        from gtfsdb import UniversalCalendar, Route, Stop

        routes = session.query(RouteStop).all()
        for rs in routes:
            q = session.query(func.min(UniversalCalendar.date), func.max(UniversalCalendar.date))
            q = q.filter(UniversalCalendar.trips.any(route_id=rs.route_id))
            q = q.filter(UniversalCalendar.trips.stop_times.any(stop_id=rs.stop_id))
            print q.all()

        #import pdb; pdb.set_trace()
        #session.query(Route.route_id, Stop.stop_id, func.min(U.date) ).group_by(Table.column1, Table.column2).all()
        #q = session.query(func.min(UniversalCalendar.date) ).group_by(Table.column1, Table.column2).all()