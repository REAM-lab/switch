#Author: Natalia Gonzalez
from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from switch_model.financials import capital_recovery_factor as crf

import os
from pyomo.environ import *
import pandas as pd

dependencies = (
    'switch_model.timescales',
    'switch_model.balancing.load_zones',
    'switch_model.financials',
    'switch_model.generators.core.build',
    'switch_model.generators.core.dispatch',
    'switch_model.generators.core.commit.operate',
)

def define_components(mod):
    """
    PARAMETERS AND SETS:

    OFFSHORE_SITES is the set of offshore sites numbered 1 to 101 (in this version 
    there are 101 offshore sites that can have offshore wind and/or wave energy 
    installed, but more can be added to the DB).

    wave_id[s] is a parameter indexed by offshore site (s) that contains the 
    GENERATION_PROJECT id for the wave energy project of each offshore site.

    osw_id[s] is a parameter indexed by offshore site (s) that contains the 
    GENERATION_PROJECT id for the offshore wind energy project of each offshore site.

    WAVE_OSW_PAIRS[s] is a set indexed by offshore site (s) that contains tuples of 
    GENERATION_PROJECT ids corresponding to the wave and offshore wind project pair
    of each offshore site.

    OFFSHORE_PROJECTS is a set containing the GENERATION_PROJECT ids associated with 
    all wave and offshore wind projects found in WAVE_OSW_PAIRS.csv

    ONSHORE_PROJECTS is a set containing all GENERATION_PROJECT ids found in the set
    mod.GENERATION_PROJECTS that are not included in OFFSHORE_PROJECT (i.e. ids of all 
    land-based projects)

    OFFSHORE_GEN_BLD_YRS is a set containing (g, build_year) tuples for each offshore
    project (g) and the corresponding years that they can be built (build_year).

    HYBRID_GEN_BLD_YRS is a set containing (s, build_year) tuples for each hybrid
    project (s) and the corresponding years that they can be built (build_year). The 
    years which hybrid projects can be built are the same as the years which the 
    corresponding wave energy project can be built.

    gen_capacity_limit_mw_hybrid[s] is a parameter indexed by offshore site (s) that 
    represents the capacity limit of each hybrid wave-wind farm

    overnight_discount is a parameter (scalar) that represents the discount given to 
    overnight costs for offshore projects exhibiting collocation. 
    0 <= overnight_discount <= 1. A overnight_discount of 0.8 = 20% savings.

    om_discount is a parameter (scalar) that represents the discount given to O&M
    costs for offshore projects exhibiting collocation. 0 <= om_discount <= 1. 
    A om_discount of 0.75 = 25% savings.

    connection_discount is a parameter (scalar) that represents the discount given to 
    connection costs for offshore projects exhibiting collocation. 
    0 <= connection_discount <= 1. A connection_discount of 0.9 = 10% savings.

    min_cap_mw_offshore is a parameter (scalar) that represents the minimum capacity 
    that an offshore project must build for BUILT = 1 to be true.

    DECISION VARIABLES:

    BUILT[g, build_year] is a binary decision variable indexed by offshore id (g) and the 
    years in which construction or expansion occured or can occur (build_year) that 
    represents whether or not the corresponding wave or offshore wind project gets built
    in each build_year. 

    BUILT_hybrid[s, build_year] is a binary decision variable indexed by offshore site (s) 
    and the years in which construction or expansion occured or can occur (build_year) that 
    represents whether or not the corresponding hybrid wave-wind project gets built in each
    build_year. 

    BuildGenHybrid[g, build_year] is a decision variable indexed by offshore id (g) 
    and the years in which construction or expansion occured or can occur (build_year) that 
    represents how much offshore wind and wave energy capacity (which are part of wave-wind
    hybrid farms) gets built in a given year.

    EXPRESSIONS:

    GenCapacityHybrid[g, period] is an expression indexed by offshore id (g) and period (p)
    that returns the total capacity online in a given period. This is the sum of installed 
    capacity minus all retirements. The generators in this expression are offshore wind or 
    wave energy projects that are part of hybrid wave-wind farms.
    
    """
    #Defining sets and parameters 
    # mod.OFFSHORE_SITES = Set(ordered=False, dimen=1, input_file="collocation_inputs/wave_osw_pairs.csv", input_column="offshore_site")
    # mod.wave_id = Param(mod.OFFSHORE_SITES, within=PositiveIntegers, input_file="collocation_inputs/wave_osw_pairs.csv", input_column="wave_id")
    # mod.osw_id = Param(mod.OFFSHORE_SITES, within=PositiveIntegers, input_file="collocation_inputs/wave_osw_pairs.csv", input_column="osw_id")
    mod.OFFSHORE_WO = Set(ordered=False, dimen=3, input_file="collocation_inputs/wave_osw_pairs.csv")
    mod.OFFSHORE_SITES = Set(ordered=False, dimen=1, initialize= lambda m: set(s for (s, w, osw) in m.OFFSHORE_WO))
    mod.WAVE = Set(ordered=False, dimen=1, initialize= lambda m: set(w for (s, w, osw) in m.OFFSHORE_WO))
    mod.OSW = Set(ordered=False, dimen=1, initialize= lambda m: set(osw for (s, w, osw) in m.OFFSHORE_WO))
    mod.OFFSHORE_PROJECTS = Set(dimen=1, initialize= lambda m: m.WAVE | m.OSW)
    
    def init_wave_osw_site_map(m, g):
        for (s, w, osw) in m.OFFSHORE_WO:
            if w==g or osw==g:
                return s
    mod.WAVE_OSW_SITE_MAP = Param(
        mod.OFFSHORE_PROJECTS,
        initialize= init_wave_osw_site_map)

    # mod.WAVE_OSW_PAIRS = Set(
    #     mod.OFFSHORE_SITES, 
    #     ordered=True, 
    #     dimen=2,
    #     initialize=lambda m, s: (
    #         (m.wave_id[s], m.osw_id[s])))
     
    # mod.OFFSHORE_PROJECTS = Set(ordered=True, dimen=1, within=PositiveIntegers)
    # for s in mod.OFFSHORE_SITES:
    #     mod.OFFSHORE_PROJECTS.add(mod.wave_id[s])
    #     mod.OFFSHORE_PROJECTS.add(mod.osw_id[s])

    # mod.ONSHORE_PROJECTS = Set(
    #     dimen=1, 
    #     initialize= mod.GENERATION_PROJECTS - mod.OFFSHORE_PROJECTS)
    
    # mod.OFFSHORE_GEN_BLD_YRS = Set(
    #     dimen=2,
    #     initialize=mod.NEW_GEN_BLD_YRS,
    #     filter=lambda m, g, p: (
    #             g in m.OFFSHORE_PROJECTS))
    
    # def initialize_hybrid_gen_bld_yrs_rule(mod):
    #     return ((s,bld_yr) for s in mod.OFFSHORE_SITES for bld_yr in mod.BLD_YRS_FOR_GEN[mod.wave_id[s]])

    # mod.HYBRID_GEN_BLD_YRS = Set(
    #     dimen=2,
    #     initialize=initialize_hybrid_gen_bld_yrs_rule)

    # mod.gen_capacity_limit_mw_hybrid = Param(
    #     mod.OFFSHORE_SITES, 
    #     within=NonNegativeReals,
    #     initialize=lambda m, s: (
    #         m.gen_capacity_limit_mw[m.wave_id[s]] + m.gen_capacity_limit_mw[m.osw_id[s]]))

    # mod.overnight_discount = Param(within=PercentFraction, input_file="collocation_inputs/collocation_params.csv", input_column="overnight_discount")
    # mod.om_discount = Param(within=PercentFraction, input_file="collocation_inputs/collocation_params.csv", input_column="om_discount")
    # mod.connection_discount = Param(within=PercentFraction, input_file="collocation_inputs/collocation_params.csv", input_column="connection_discount")
    # mod.min_cap_mw_offshore = Param(within=NonNegativeReals, input_file="collocation_inputs/collocation_params.csv", input_column="min_cap_mw")

    # mod.GEN_TPS_HYB = Set(
    #     dimen=2,
    #     initialize=lambda m: (
    #         (g, tp)
    #             for g in m.OFFSHORE_PROJECTS
    #                 for tp in m.TPS_FOR_GEN[g]))
    
    # mod.GENS_FOR_ZONE_TPS_HYB = Set(
    #     mod.LOAD_ZONES, mod.TIMEPOINTS,
    #     ordered=False,
    #     initialize=lambda m, z, t: set(g for g in m.GENS_IN_ZONE[z] if (g, t) in m.GEN_TPS_HYB))
    
    # mod.gen_max_commit_fraction_hybrid = Param(
    #     mod.GEN_TPS_HYB,
    #     within=PercentFraction,
    #     default=lambda m, g, t: 1.0)

    # #Defining decision variables  
    # mod.BUILT = Var(
    #     mod.OFFSHORE_GEN_BLD_YRS,
    #     within=Binary)
    
    # mod.BUILT_hybrid = Var(
    #     mod.HYBRID_GEN_BLD_YRS,
    #     within=Binary)
    
    # mod.BuildGenHybrid = Var(
    #     mod.OFFSHORE_GEN_BLD_YRS,
    #     within=NonNegativeReals)

    # mod.DispatchGenHybrid = Var(
    #     mod.GEN_TPS_HYB,
    #     within=NonNegativeReals)
    
    # mod.CommitGenHybrid = Var(
    #     mod.GEN_TPS_HYB,
    #     within=NonNegativeReals)
    
    # #Defining expressions
    # mod.GenCapacityHybrid = Expression(
    #     mod.OFFSHORE_PROJECTS, mod.PERIODS,
    #     rule=lambda m, g, period: sum(
    #         m.BuildGenHybrid[g, bld_yr]
    #         for bld_yr in m.BLD_YRS_FOR_GEN_PERIOD[g, period]))
    
    # mod.GenCapacityInTPHybrid = Expression(
    #     mod.GEN_TPS_HYB,
    #     rule=lambda m, g, t: m.GenCapacityHybrid[g, m.tp_period[t]])
    
    # mod.CommitUpperLimitHybrid = Expression(
    #     mod.GEN_TPS_HYB,
    #     rule=lambda m, g, t: (
    #         m.GenCapacityInTPHybrid[g, t] * m.gen_availability[g] *
    #         m.gen_max_commit_fraction_hybrid[g, t]))
    
    # mod.CommitSlackUpHybrid = Expression(
    #     mod.GEN_TPS_HYB,
    #     rule=lambda m, g, t: (
    #         m.CommitUpperLimitHybrid[g, t] - m.CommitGenHybrid[g, t]))
    
    # mod.DispatchUpperLimitHybrid = Expression(
    #     mod.GEN_TPS_HYB,
    #     rule=lambda m, g, t: (
    #         m.CommitGenHybrid[g, t]*m.gen_max_capacity_factor[g, t]))
    
    # mod.gen_capital_cost_annual_hybrid = Param(
    #     mod.OFFSHORE_GEN_BLD_YRS,
    #     initialize=lambda m, g, bld_yr: (
    #         (m.gen_overnight_cost[g, bld_yr]*m.overnight_discount +
    #             m.gen_connect_cost_per_mw[g]*m.connection_discount) *
    #         crf(m.interest_rate, m.gen_max_age[g])))

    # mod.GenCapitalCostsHybrid = Expression(
    #     mod.OFFSHORE_PROJECTS, mod.PERIODS,
    #     rule=lambda m, g, p: sum(
    #         m.BuildGenHybrid[g, bld_yr] * m.gen_capital_cost_annual_hybrid[g, bld_yr]
    #         for bld_yr in m.BLD_YRS_FOR_GEN_PERIOD[g, p]))
    
    # mod.GenFixedOMCostsHybrid = Expression(
    #     mod.OFFSHORE_PROJECTS, mod.PERIODS,
    #     rule=lambda m, g, p: sum(
    #         m.BuildGenHybrid[g, bld_yr] * m.gen_fixed_om[g, bld_yr] * m.om_discount
    #         for bld_yr in m.BLD_YRS_FOR_GEN_PERIOD[g, p]))

    # mod.TotalGenFixedCostsHybrid = Expression(
    #     mod.PERIODS,
    #     rule=lambda m, p: sum(
    #         m.GenCapitalCostsHybrid[g, p] + m.GenFixedOMCostsHybrid[g, p]
    #         for g in m.OFFSHORE_PROJECTS))
    # mod.Cost_Components_Per_Period.append('TotalGenFixedCostsHybrid')

    # mod.ZoneTotalCentralDispatchHybrid = Expression(
    #     mod.LOAD_ZONES, mod.TIMEPOINTS,
    #     rule=lambda m, z, t: \
    #     sum(m.DispatchGenHybrid[g, t]
    #         for g in m.GENS_FOR_ZONE_TPS_HYB[z, t]),
    #     doc="Net power from grid-tied hybrid wave-wind generation projects.")
    # mod.Zone_Power_Injections.append('ZoneTotalCentralDispatchHybrid')

    # mod.GenVariableOMCostsInTPHybrid = Expression(
    #     mod.TIMEPOINTS,
    #     rule=lambda m, t: sum(
    #         m.DispatchGenHybrid[g, t] * m.gen_variable_om[g] * m.om_discount
    #         for g in m.GENS_IN_PERIOD[m.tp_period[t]] if g in m.OFFSHORE_PROJECTS),
    #     doc="Summarize hybrid wave-wind project variable O&M costs for the objective function")
    # mod.Cost_Components_Per_TP.append('GenVariableOMCostsInTPHybrid')

    # #Defining constraints
    # mod.Enforce_Min_Build_Offshore = Constraint(
    #     mod.OFFSHORE_GEN_BLD_YRS,
    #     rule=lambda m, g, p: (
    #         m.min_cap_mw_offshore * m.BUILT[g, p]
    #         <= m.BuildGen[g, p]))
    
    # max_build_potential_scaling_factor = 1e-1
    # mod.max_build_potential_offshore = Constraint(
    #     mod.OFFSHORE_PROJECTS, mod.PERIODS,
    #     rule=lambda m, g, p: (
    #             m.gen_capacity_limit_mw[g] * m.BUILT[g, p] * max_build_potential_scaling_factor 
    #             >= m.GenCapacity[g, p] * max_build_potential_scaling_factor))
    
    # mod.Enforce_Min_Build_Hybrid = Constraint(
    #     mod.OFFSHORE_GEN_BLD_YRS,
    #     rule=lambda m, g, p: (
    #         m.min_cap_mw_offshore * m.BUILT_hybrid[m.WAVE_OSW_SITE_MAP[g], p]
    #         <= m.BuildGenHybrid[g, p]))
    
    # mod.max_build_potential_hybrid = Constraint(
    #     mod.OFFSHORE_PROJECTS, mod.PERIODS,
    #     rule=lambda m, g, p: (
    #             m.gen_capacity_limit_mw[g] * m.BUILT_hybrid[m.WAVE_OSW_SITE_MAP[g], p] * max_build_potential_scaling_factor 
    #             >= m.GenCapacityHybrid[g, p] * max_build_potential_scaling_factor))
    
    # mod.MutallyExclusive = Constraint(
    #     mod.OFFSHORE_SITES, mod.PERIODS,
    #     rule=lambda m, s, p: 
    #         m.BUILT[m.wave_id[s], p] + m.BUILT[m.osw_id[s], p] + m.BUILT_hybrid[s, p] <= 1)
    
    # mod.Enforce_Commit_Upper_Limit_Hybrid = Constraint(
    #     mod.GEN_TPS_HYB,
    #     rule=lambda m, g, t: (
    #         m.CommitGenHybrid[g, t] <= m.CommitUpperLimitHybrid[g, t]))
    
    # mod.Enforce_Dispatch_Upper_Limit_Hybrid = Constraint(
    #     mod.GEN_TPS_HYB,
    #     rule=lambda m, g, t: (
    #         m.DispatchGenHybrid[g, t] <= m.CommitGenHybrid[g, t]*m.gen_max_capacity_factor[g, t]))
    
    # mod.DispatchSlackUpHybrid = Expression(
    #     mod.GEN_TPS_HYB,
    #     rule=lambda m, g, t: (
    #         m.DispatchUpperLimitHybrid[g, t] - m.DispatchGenHybrid[g, t]))
    
    # mod.DispatchSlackDownHybrid = Expression(
    #     mod.GEN_TPS_HYB,
    #     rule=lambda m, g, t: (
    #         m.DispatchGenHybrid[g, t] - m.DispatchLowerLimitHybrid[g, t]))
    
    # #TO-DO:
    # #Output files