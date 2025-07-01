from __future__ import division
import os
from pyomo.environ import *
from switch_model.financials import capital_recovery_factor as crf
from switch_model.reporting import write_table
from switch_model.tools.graph import graph
import pandas as pd

dependencies = ("switch_model.timescales")

def define_arguments(argparser):
    argparser.add_argument(
        "--no-hydrogen",
        action="store_true",
        default=False,
        help="Don't allow construction of any hydrogen infrastructure.",
    )

def define_components(m):
    if not m.options.no_hydrogen:
        define_hydrogen_components(m)

def define_hydrogen_components(m):
    """
    Users can use the hydrogen_timepoints.csv, hydrogen_timeseries.csv and
    hydrogen_periods.csv files to customize the hydrogen storage duration and cycle.

    ------------------------------------------
    hydrogen_timepoints.csv:
    The hydrogen_timepoints.csv input file must include all the timepoint_ids in the 
    switch timepoints.csv input file. The hydrogen_timeseries column contains the new
    timeseries names that correspond to the maximum frequency at which hydrogen will 
    be stored or withdrawn from liquid storage. For example, if hydrogen can be stored 
    daily (but not hourly) to the tank, timepoints would be grouped into daily time 
    series regardless of how long the main time series are in the switch timeseries.csv 
    input file. The only requirement is that the hydrogen timeseries (hgts) must be
    equal to or of a longer duration than the timepoints it includes. 
    hydrogen_timepoints.csv
        timepoint_id, hydrogen_timeseries

    ------------------------------------------
    hydrogen_timeseries.csv:
    The hydrogen_timeseries.csv input file allows users to further describe the hydrogen timeseries
    defined in hydrogen_timepoints.csv and specify a new HYDROGEN PERIOD (hgp) that corresponds to how
    often hydrogen storage is cycled. For example, if hydrogen should not be stored for longer than 1
    month, then each hgp would represent a one month period. Hydrogen storage is constrained to have 
    zero net hydrogen stored from one hgp to the next hgp (H2 at hgp start - H2 at hgp end = 0). 
    The hydrogen_timseries.csv input file must include the following:
        HYDROGEN_TIMESERIES: the exact same hydrogen timeseries names defined in hydrogen_timepoints.csv
        hgts_period: the PERIOD (from the switch timeseries.csv input file) containing the hydrogen timeseries in col 1
        hgts_hydrogen_period: the NEW HYDROGEN PERIOD containing the hydrogen timeseries in col 1
        hgts_duration_of_tp: the duration in hours of the timepoints in the hgts in col 1. Must match the
        ts_duration_of_tp for the corresponding timeseries in switch
        hgts_scale_to_hgp: the number of times that the hgts in col 1 occurs in the hgp
    The file format is as follows. 
    hydrogen_timeseries.csv
        HYDROGEN_TIMESERIES,hgts_period,hgts_hydrogen_period,hgts_duration_of_tp,ts_duration_of_tp,
        hgts_scale_to_hgp

    ------------------------------------------
    hydrogen_periods.csv:
    The hydrogen_periods.csv input file maps hydrogen periods to the switch model periods. 
    It must include the following:
    hydrogen_periods.csv
        hydrogen_period, period
    where hydrogen_period exactly matches the hgp in hydrogen_timeseries.csv and period exactly matches
    the periods in periods.csv.
    """
    
    # HYDROGEN TIMESCALES DETAILS
    m.tp_to_hgts = Param(
        m.TIMEPOINTS,
        input_file='hydrogen_timepoints.csv',
        input_column='hydrogen_timeseries',
        default=lambda m, tp: m.tp_ts[tp], #default is to use the main model time series 
        doc="Mapping of timepoints to a hydrogen timeseries.",
        within=Any
    )
    m.HGTS = Set(
        dimen=1,
        ordered=False,
        initialize=lambda m: set(m.tp_to_hgts[tp] for tp in m.TIMEPOINTS),
        doc="Set of hydrogen timeseries that correspond to max storage frequency as defined in the mapping."
    )

    m.hgts_period = Param(
        m.HGTS,
        input_file='hydrogen_timeseries.csv',
        input_column='hgts_period',
        doc="Mapping of hydrogen time series to the main model periods.",
        within=m.PERIODS
    )
    m.hgts_hg_period = Param(
        m.HGTS,
        input_file='hydrogen_timeseries.csv',
        input_column='hgts_hydrogen_period',
        doc="Mapping of hydrogen time series to the hydrogen periods.",
        within=Any
    )
    m.HGP = Set(
        dimen=1,
        ordered=False,
        initialize=lambda m: set(m.hgts_hg_period[hgts] for hgts in m.HGTS),
        doc="Set of hydrogen periods that correspond to the storage cycling period."
    )
    m.TPS_IN_HGTS = Set(
        m.HGTS,
        within=m.TIMEPOINTS,
        ordered=False,
        initialize=lambda m, hgts: set(t for t in m.TIMEPOINTS if m.tp_to_hgts[t] == hgts),
        doc="Set of timepoints in each hydrogen timeseries."
    )
    m.HGTS_IN_HGP = Set(
        m.HGP,
        within=m.HGTS,
        ordered=False,
        initialize=lambda m, hgp: set(hgts for hgts in m.HGTS if m.hgts_hg_period[hgts] == hgp),
        doc="Set of hydrogen time series in each hydrogen period."
    )
    m.HGTS_IN_PERIOD = Set(
        m.PERIODS,
        within=m.HGTS,
        ordered=False,
        initialize=lambda m, p: set(hgts for hgts in m.HGTS if m.hgts_period[hgts] == p),
        doc="Set of hydrogen time series in each main model period."
    )

    m.hgts_duration_of_tp = Param(
        m.HGTS,
        within=PositiveReals,
        input_file='hydrogen_timeseries.csv',
        input_column='hgts_duration_of_tp',
        doc="Duration in hours of the timepoints in each hydrogen time series"
    )
    m.hgts_scale_to_hgp = Param(
        m.HGTS,
        within=PositiveReals,
        input_file='hydrogen_timeseries.csv',
        input_column='hgts_scale_to_hgp',
        doc="Number of times a hydrogen time series occurs in its hydrogen period"
    )
    m.hgp_p = Param(
        m.HGP,
        within=m.PERIODS,
        input_file="hydrogen_periods.csv",
        input_column="period",
        doc="Mapping of hydrogen periods to normal model periods."
    )