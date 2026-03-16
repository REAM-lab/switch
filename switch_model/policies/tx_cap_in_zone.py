# Copyright (c) 2015-2023 The Switch Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0, which is in the LICENSE file.
# Module created in Sep, 2023.


'''
Purpose of the module:
    This module creates constraints that restrict the sum of built transmission 
    (decision variable named BuildTX) in a given period for groups of load zones.  
    The groups of load zones, lower bound and upper bounds of transmission 
    capacity are set by the user. 

Input files:
    The following two files are necessary to run the module.

    (1) tx_cap_zones.csv
        It has two columns: GROUP, LOAD_ZONE. The column GROUP is the name of 
        the groups for which a transmission cap will be set. 
        The column LOAD_ZONE are the load zones for each group.
        An example of the file as follows:
        
        TX_CAP_GROUPS | LOAD_ZONE
        california    | CA_SDGE
        california    | CA_SCE_S
        california    | CA_SCE_VLY
        oregon        | OR_E
        oregon        | OR_W

    (2) tx_cap_bounds.csv
        It has the lower and upper bounds for each period for the groups listed 
        in tx_cap_zones.
        "." means zero in tx_cap_lowerbound_mw
        "." means infinity in tx_cap_upperbound_mw 
        An example of the file as follows:

        TX_CAP_GROUPS | PERIODS | tx_cap_upperbound_mw | tx_cap_lowerbound_mw
        california    | 2020    | .                     | 1000
        california    | 2030    | .                     | .
        oregon        | 2020    | 1                     | 500
'''

import os
from pyomo.environ import *
from switch_model.reporting import write_table

def define_components(mod):
    '''

    Adds components to a Pyomo abstract model object to set 
    constraints on the built transmission capacity of load zones.
    This includes parameters, build decisions and constraints. 
    Unless otherwise stated, all power capacity is specified in 
    units of MW and all sets and parameters are mandatory.

    TX_CAP_GROUPS is the one-dimensional set of the groups 
    for which a transmission cap will set to the build transmission.

    TX_CAP_LOAD_ZONES is a two-dimensional set that relates 
    the groups in TX_CAP_GROUPS with load zones of the set 
    LOAD_ZONES. 

    TX_GROUP_PERIODS is a two-dimensional set that relates
    the groups of TX_CAP_GROUPS with periods.

    tx_cap_lowerbound_mw[(group, period) in TX_GROUP_PERIODS] is a 
    parameter that spits out the lower bound to be set to the 
    transmission built for the given group of TX_CAP_GROUPS, and 
    any given period.

    tx_cap_upperbound_mw[(group, period) in TX_GROUP_PERIODS] is a 
    parameter that spits out the upper bound to be set to the 
    transmission built for any given group of TX_CAP_GROUPS, and 
    given period.

    zone_online_tx_capacity[(group, period) in TX_GROUP_PERIODS] is 
    a expression that provides the total online transmission capacity
    (TxCapacityNameplate) of any given group of TX_CAP_GROUPS, and 
    any given period.

    zone_built_tx_capacity[(group, period) in TX_GROUP_PERIODS] is a 
    parameter that provides the total built transmission capacity
    (BuildTX) of any given group of TX_CAP_GROUPS, and any given period.
    We remark that it is only the built tranmission for the period, 
    and not the accumulated up to the period.

    zone_existing_tx_capacity[group in TX_CAP_GROUPS] is a 
    parameter that provides the total existing transmission capacity
    (existing_trans_cap) of any given group of TX_CAP_GROUPS.
    We remark that since the existing tx capacity is the initial 
    transmission capacity (prior the optimization) 

    zone_tx_cap_lowerbound_constraint[(group, period) in TX_GROUP_PERIODS]
    is a constraint that restricts the
    built transmission of the (group, period) by below with the value 
    of the parameter tx_cap_lowerbound_mw 

    zone_tx_cap_upperbound_constraint[(group, period) in TX_GROUP_PERIODS]
    is a constraint that restricts the
    built transmission of the (group, period) by above with the value 
    of the parameter tx_cap_upperbound_mw 

    For code developers:
    For the two set of constraints: You may replace m.zone_built_tx_capacity 
    with m.zone_online_tx_capacity if you want to limit the total cap by period.
    '''       

    mod.TX_CAP_LOAD_ZONES = Set( dimen=2,
                                 input_file="tx_cap_zones.csv",
                                 doc="Sets that match groups with zones")
        
    # This verifies if the load zones entered in the column of load zones of 
    # "tx_cap_zones.csv" belong to the set LOAD ZONES. If not, the construction 
    # of the model is stopped.
    mod.loadzone_not_recognized_in_tx_cap_zones = BuildCheck( mod.TX_CAP_LOAD_ZONES,
                                         rule=lambda m, g, lz: lz in m.LOAD_ZONES
                                         )
        
    mod.TX_CAP_GROUPS = Set(  dimen=1,
                              ordered=False,
                              initialize=lambda m: set(g for (g, lz) in m.TX_CAP_LOAD_ZONES),
                              doc="Group of load zones for which the user wants to set a transmission cap")
        
    mod.TX_GROUP_PERIODS = Set( 
                               within=mod.TX_CAP_GROUPS * mod.PERIODS,
                               input_file="tx_cap_bounds.csv",
                               input_optional=True,
                               dimen=2,
                               doc="The cross product of groups and periods")       
        
    mod.tx_cap_lowerbound_mw = Param( mod.TX_GROUP_PERIODS,
                                      within=NonNegativeReals, 
                                      default=float(0),
                                      input_file="tx_cap_bounds.csv",
                                      doc="lower bound of the built transmission capacity set to the (group, period)")
       
    mod.tx_cap_upperbound_mw = Param( mod.TX_GROUP_PERIODS,
                                      within=NonNegativeReals, 
                                      default=float('inf'),
                                      input_file="tx_cap_bounds.csv",
                                      doc="upper bound of the built transmission capacity set to the (group, period)")
    
    # This verifies if the lower bound is less or equal than the upper bound for 
    # row in the file "tx_cap_bouns.csv"
    mod.lowerbound_less_greater_than_upperbound_tx_bounds = BuildCheck( mod.TX_GROUP_PERIODS,
                                         rule=lambda m, g, p: m.tx_cap_lowerbound_mw[g,p]<=m.tx_cap_upperbound_mw[g,p]
                                         )
    
    mod.zone_online_tx_capacity = Expression( mod.TX_GROUP_PERIODS,
                                              rule=lambda m, g, p: sum(
                                              m.TxCapacityNameplate[tx, p] for tx in m.TRANSMISSION_LINES 
                                              if (((g,m.trans_lz1[tx]) in m.TX_CAP_LOAD_ZONES) 
                                                & ((g,m.trans_lz2[tx]) in m.TX_CAP_LOAD_ZONES)))
                                                )
 
    mod.zone_built_tx_capacity = Expression( mod.TX_GROUP_PERIODS,
                                             rule=lambda m, g, p: sum(
                                             m.BuildTx[tx, p] for tx in m.TRANSMISSION_LINES 
                                             if (((g,m.trans_lz1[tx]) in m.TX_CAP_LOAD_ZONES) 
                                                     & ((g,m.trans_lz2[tx]) in m.TX_CAP_LOAD_ZONES) 
                                                     & ((tx, p) in m.BuildTx)))
                                             )
 
    mod.zone_existing_tx_capacity = Expression( mod.TX_CAP_GROUPS,
                                                rule=lambda m, g: sum(
                                                m.existing_trans_cap[tx] for tx in m.TRANSMISSION_LINES 
                                                if (((g,m.trans_lz1[tx]) in m.TX_CAP_LOAD_ZONES) 
                                                    & ((g,m.trans_lz2[tx]) in m.TX_CAP_LOAD_ZONES)))
                                                    )

    mod.zone_tx_cap_lower_bound_constraint = Constraint( mod.TX_GROUP_PERIODS,
                                                         rule=lambda m, g, p: (m.zone_built_tx_capacity[g,p] >= m.tx_cap_lowerbound_mw[g,p])
                                                                              if m.tx_cap_lowerbound_mw[g,p] != 0 else Constraint.Skip
                                                        )
        
    mod.zone_tx_cap_upper_bound_constraint = Constraint( mod.TX_GROUP_PERIODS,
                                                         rule=lambda m, g, p: (m.zone_built_tx_capacity[g,p] <= m.tx_cap_upperbound_mw[g,p])
                                                                              if m.tx_cap_upperbound_mw[g,p] != float('inf') else Constraint.Skip
                                                            )
def post_solve(model, outdir):
                    write_table(
                    model, model.TX_GROUP_PERIODS,
                    output_file=os.path.join(outdir, "tx_cap_built.csv"),
                    headings=("TX_CAP_GROUP", "PERIOD", "prior_period_existing_mw", "built_for_period_mw", 
                              "total_in_period_mw", "tx_cap_lowerbound_mw", ".tx_cap_upperbound_mw"),
                    values= lambda m, g, p: [
                                            g,
                                            p,
                                            m.zone_existing_tx_capacity[g] if m.PERIODS.ord(p)==1 else value(m.zone_online_tx_capacity[g,m.PERIODS.prev(p)]),
                                            value(m.zone_built_tx_capacity[g,p]),
                                            value(m.zone_online_tx_capacity[g,p]),
                                            m.tx_cap_lowerbound_mw[g,p],
                                            m.tx_cap_upperbound_mw[g,p]
                                            ]       
    )


