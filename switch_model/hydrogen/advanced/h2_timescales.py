from __future__ import division
from pyomo.environ import *

dependencies = ("switch_model.timescales")

def define_components(m):
    """
    Users can use the h2_timepoints.csv and h2_timeseries.csv files to customize the 
    hydrogen storage cycle.

    ------------------------------------------
    h2_timepoints.csv:
    The h2_timepoints.csv input file must include all the timepoint_ids in the 
    switch timepoints.csv input file. The hydrogen_timeseries column contains the new
    timeseries names that correspond to the maximum frequency at which hydrogen will 
    be stored or withdrawn from storage. For example, if hydrogen can be stored 
    daily (but not hourly) to the tank, timepoints would be grouped into daily time 
    series regardless of how long the main time series are in the switch timeseries.csv 
    input file. The only requirement is that the hydrogen timeseries (hgts) must be
    equal to or of a longer duration than the timepoints it includes. The "timestamp"
    column does not get used in the formulation, but it is included to give meaning to
    the timepoint_id values for data analysis purposes, formatted YYYMMDDHH.
    h2_timepoints.csv
        timepoint_id, timestamp, hydrogen_timeseries

    ------------------------------------------
    h2_timeseries.csv:
    The h2_timeseries.csv input file allows users to further describe the hydrogen timeseries
    defined in h2_timepoints.csv and specify the main model period that the hydrogen timeseries belongs to. 
    For example, if hydrogen state of "charge", or state of fill, should be equal at the start and end of 
    each month, then the hydrogen timeseries should correspond to 1 month. 
    The hydrogen_timseries.csv input file must include the following:
    
        HYDROGEN_TIMESERIES: the exact same hydrogen timeseries names defined in h2_timepoints.csv.
        
        hgts_period: the PERIOD (from the switch timeseries.csv input file) containing the hydrogen 
        timeseries in col 1.
        
        hgts_duration_of_tp: the duration in hours of the timepoints in the hgts in col 1. Must match the
        ts_duration_of_tp for the corresponding timeseries in switch.
        
    The file format is as follows. 
    h2_timeseries.csv
        HYDROGEN_TIMESERIES, hgts_period, hgts_duration_of_tp

    """
    
    # HYDROGEN TIMESCALES DETAILS
    m.tp_to_hgts = Param(
        m.TIMEPOINTS,
        input_file='h2_timepoints.csv',
        input_column='hydrogen_timeseries',
        default=lambda m, tp: m.tp_ts[tp], #default is to use the main model time series 
        doc="Mapping of timepoints to a hydrogen timeseries.",
        within=Any
    )
    m.HGTS = Set(
        dimen=1,
        ordered=True,
        initialize=lambda m: set(m.tp_to_hgts[tp] for tp in m.TIMEPOINTS),
        doc="Set of hydrogen timeseries that correspond to max storage frequency as defined in the mapping."
    )

    m.hgts_period = Param(
        m.HGTS,
        input_file='h2_timeseries.csv',
        input_column='hgts_period',
        doc="Mapping of hydrogen time series to the main model periods.",
        within=m.PERIODS
    )
    m.TPS_IN_HGTS = Set(
        m.HGTS,
        within=m.TIMEPOINTS,
        ordered=True,
        initialize=lambda m, hgts: sorted(
			[t for t in m.TIMEPOINTS if m.tp_to_hgts[t] == hgts],
			key=lambda t: m.tp_timestamp[t]
		),
        doc="Set of ordered timepoints in each hydrogen timeseries."
    )
    m.HGTS_IN_PERIOD = Set(
        m.PERIODS,
        within=m.HGTS,
        ordered=True,
        initialize=lambda m, p: set(hgts for hgts in m.HGTS if m.hgts_period[hgts] == p),
        doc="Set of hydrogen time series in each main model period."
    )
    m.hgts_duration_of_tp = Param(
        m.HGTS,
        within=PositiveReals,
        input_file='h2_timeseries.csv',
        input_column='hgts_duration_of_tp',
        doc="Duration in hours of the timepoints in each hydrogen time series"
    )
    # Identify previous step for each timepoint, for use in tracking
    # H2 storage. We use circular indexing (.prevw() method) for the
    # timepoints within a timeseries to give consistency between the
    # start and end state. (Note: separate timeseries are assumed to be
    # disconnected from each other.)
    m.h2_tp_previous = Param(
        m.TIMEPOINTS,
        within=m.TIMEPOINTS,
        initialize=lambda m, t: m.TPS_IN_HGTS[m.tp_to_hgts[t]].prevw(t))