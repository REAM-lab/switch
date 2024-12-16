#Author: Natalia Gonzalez
# from __future__ import print_function
# from __future__ import absolute_import
# from __future__ import division
from switch_model.financials import capital_recovery_factor as crf
from switch_model.reporting import write_table
from switch_model.tools.graph import graph

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

    OFFSHORE_WO is the set of (site id, wave id, osw id) tuples that represent the site id
    and corresponding wave and offshore wind energy generation project ids.

    OFFSHORE_SITES is the set of offshore sites numbered 1 to 101 (in this version there
    are 101 offshore sites that can have offshore wind and/or wave energy installed, but
    more can be added to the DB).

    WAVE is the set of GENERATION_PROJECT ids for all wave energy projects.

    OSW is the set of GENERATION_PROJECT ids for all offshore wind energy projects.

    OFFSHORE_PROJECTS is the set of all GENERATION_PROJECT ids associated with the wave and
    offshore wind projects found in WAVE_OSW_PAIRS.csv

    wave_osw_site_map is a parameter indexed by offshore project that maps a project (g) to
    its corresponding site (s).

    s_to_w_map is a parameter indexed by offshore site that maps a site (s) to its
    corresponding wave energy project (w).

    s_to_o_map is a parameter indexed by offshore site that maps a site (s) to its
    corresponding offshore wind energy project (o).

    OFFSHORE_GEN_BLD_YRS is a set containing (g, build_year) tuples for each offshore
    project (g) and the corresponding years that they can be Built (build_year).

    HYBRID_GEN_BLD_YRS is a set containing (s, build_year) tuples for each hybrid project
    (s) and the corresponding years that they can be Built (build_year). The years which
    hybrid projects can be Built are the same as the years which the corresponding wave
    energy project can be Built.

    overnight_discount is a parameter (scalar) that represents the discount given to 
    overnight costs for offshore projects exhibiting collocation. 
    0 <= overnight_discount <= 1. A overnight_discount of 0.8 = 20% savings.

    om_discount is a parameter (scalar) that represents the discount given to O&M costs or
    offshore projects exhibiting collocation. 0 <= om_discount <= 1. 
    An om_discount of 0.75 = 25% savings.

    min_cap_mw_offshore is a parameter that represents the minimum capacity that 
    an offshore project must build for Built = 1 to be true. This is specified for each
    project in collocation_min_cap_hyb.csv. 

    GEN_TPS_HYB is a set of offshore projects (components of hybrid farm) and timepoints in 
    which they can be dispatched. A dispatch decisions is made for each member of this set. 
    Members of this set can be abbreviated as (g, t) or (g, t).

    GENS_FOR_ZONE_TPS_HYB is a set of hybrid generators in a specific load zone at specific 
    time points which they can be dispatched. The set is indexed by (z, t) and contains  
    only the generators which can be dispatched at time point t in zone z. 

    GEN_PERIODS_HYBRID is a set of periods in which hybrid generation projects could be
    operational. It is not indexed. Instead it is specified as a set of (g, period) 
    combinations useful for indexing other model components.

    GENERATION_TECHNOLOGIES_HYBRID is a set of gen_tech for each hybrid generator g.

    GEN_TECH_PER_PERIOD_HYBRID is a set of hybrid generation technologies and periods.

    GENS_BY_TECHNOLOGY_HYBRID is a set of generators for each gen_tech. 

    gen_max_commit_fraction_hybrid describes the maximum commit level as a fraction of 
    available capacity (capacity that is built and expected to be available for commitment;
    derated by annual expected outage rate) of hybrid generators.

    gen_min_load_fraction_hybrid describes the minimum loading level of a hybrid project as  
    a fraction of committed capacity. Note that this is only applied to committed capacity. 
    This is an optional parameter that defaults to 0. This parameter is only relevant when 
    considering unit commitment.

    gen_min_load_fraction_TP_hybrid is the same as gen_min_load_fraction_hybrid, but has 
    separate entries for each timepoint. This defaults to the value of 
    gen_min_load_fraction[g].

    gen_capital_cost_annual_hybrid is a parameter that describes the annual capital cost 
    per MW of built wave and offshore wind projects that make up hybrid farms, including 
    overnight and connection cost discounts that are specified in collocation_params.csv.

    DECISION VARIABLES:

    Built[g, build_year] is a binary decision variable indexed by offshore id (g) and the 
    years in which construction or expansion occured or can occur (build_year) that
    represents whether or not the corresponding wave or offshore wind project gets Built
    in each build_year. 

    BuiltHybrid[s, build_year] is a binary decision variable indexed by offshore site (s) 
    and the years in which construction or expansion occured or can occur (build_year) that 
    represents whether or not the corresponding hybrid wave-wind project gets Built in each
    build_year. 

    BuildGenHybrid[g, build_year] is a decision variable indexed by offshore id (g) 
    and the years in which construction or expansion occured or can occur (build_year) that 
    represents how much offshore wind and wave energy capacity (which are part of wave-wind
    hybrid farms) gets Built in a given year.

    DispatchGenHybrid[(g, t) in GEN_TPS_HYB] is the set of hybrid generation dispatch 
    decisions: how much average power in MW to produce in each timepoint. This value can be 
    multiplied by the duration of the timepoint in hours to determine the energy produced 
    by a project in a timepoint.

    CommitGenHybrid[(g, t) in GEN_TPS_HYB] is a decision variable of how much capacity (MW) 
    from each project to commit in each timepoint. By default, this operates in continuous 
    mode. 

    EXPRESSIONS:

    GenCapacityHybrid[g, period] is an expression indexed by offshore id (g) and period (p)
    that returns the total capacity online in a given period. This is the sum of installed 
    capacity minus all retirements. The generators in this expression are offshore wind or 
    wave energy projects that are part of hybrid wave-wind farms.

    GenCapacityInTPHybrid[(g, t) in GEN_TPS_HYB] is the same as GenCapacityHybrid but 
    indexed by timepoint rather than period to allow more compact statements.

    CommitUpperLimitHybrid[(g, t) in GEN_TPS_HYB] is an expression that describes the max
    hybrid capacity available for commitment. This is derived from installed capacity, 
    gen_availability and gen_max_commit_fraction_hybrid.

    CommitSlackUpHybrid[(g, t) in GEN_TPS_HYB] is an expression that describes the amount 
    of additional hybrid capacity available for commitment: CommitUpperLimitHybrid minus 
    CommitGenHybrid

    DispatchUpperLimitHybrid[(g, t) in GEN_TPS_HYB] and DispatchUpperLimitHybrid[(g, t) in 
    GEN_TPS_HYB] are expressions that define the lower and upper bounds of dispatch for 
    hybrid projects. Lower bounds are calculated as CommitGenHybrid * gen_min_cap_factor,
    and upper bounds are calculated relative to committed capacity and renewable resource 
    availability.

    GenCapitalCostsHybrid is an expression that quantifies the annual capital costs of 
    hybrid projects in dollars based on the amount of capacity built for those projects.

    GenFixedOMCostsHybrid is an expression that quantifies the annual fixed O&M costs of 
    hybrid projects in dollars based on the amount of capacity built for those projects.

    TotalGenFixedCostsHybrid is an expression that quantifies the total annual fixed 
    costs of hybrid projects in dollars. This is the sum of GenCapitalCostsHybrid and
    GenFixedOMCostsHybrid over all hybrid generators.

    ZoneTotalCentralDispatchHybrid is an expression that quantifies the net power from
    grid-tied hybrid generation projects per load zone summed over all generators in 
    that load zone and all timepoints which those generators can be dispatched. This 
    value is appended to Zone_Power_Injections.

    GenVariableOMCostsInTPHybrid is an expression that quantifies the hybrid project 
    variable O&M costs as a function of how much power is dispatched from those projects.

    GenCapacityPerTechHybrid is an expression that quantifies the amount of power 
    capacity for a period and hybrid technology.
    
    """
    #Defining sets and parameters 
    
    	#inputs
    
    mod.overnight_discount = Param(within=PercentFraction, input_file="collocation_params.csv", input_column="overnight_discount")
    mod.om_discount = Param(within=PercentFraction, input_file="collocation_params.csv", input_column="om_discount")
    
    mod.OFFSHORE_WO = Set(ordered=False, dimen=3, input_file="collocation_wave_osw_pairs.csv")
    mod.OFFSHORE_SITES = Set(within=PositiveIntegers, ordered=False, dimen=1, initialize= lambda m: set(s for (s, w, o) in m.OFFSHORE_WO))
    mod.WAVE = Set(within=mod.GENERATION_PROJECTS, ordered=False, dimen=1, initialize= lambda m: set(w for (s, w, o) in m.OFFSHORE_WO))
    mod.OSW = Set(within=mod.GENERATION_PROJECTS, ordered=False, dimen=1, initialize= lambda m: set(o for (s, w, o) in m.OFFSHORE_WO))
    mod.OFFSHORE_PROJECTS = Set(within=mod.GENERATION_PROJECTS, dimen=1, initialize= lambda m: m.WAVE | m.OSW)
    mod.min_cap_mw_offshore = Set(ordered=False, dimen=2, input_file="collocation_min_cap_hyb.csv")
    
    def init_wave_osw_site_map(m, g):
        for (s, w, osw) in m.OFFSHORE_WO:
            if w==g or osw==g:
                return s
    mod.wave_osw_site_map = Param(
        mod.OFFSHORE_PROJECTS,
        initialize= init_wave_osw_site_map)
    
    def init_s_to_w_map(m, site):
        for (s, w, o) in m.OFFSHORE_WO:
            if s == site:
                return w
    mod.s_to_w_map = Param(
        mod.OFFSHORE_SITES,
        within=Any,
        initialize= init_s_to_w_map)
    
    def init_s_to_o_map(m, site):
        for (s, w, o) in m.OFFSHORE_WO:
            if s == site:
                return o
    mod.s_to_o_map = Param(
        mod.OFFSHORE_SITES,
        within=Any,
        initialize= init_s_to_o_map)
        
    def init_g_to_min_cap_map(m, g):
        for (gpid, min_cap) in m.min_cap_mw_offshore:
            if gpid == g:
                return min_cap
    mod.g_to_min_cap_map = Param(
        mod.OFFSHORE_PROJECTS,
        within=Any,
        initialize= init_g_to_min_cap_map)
    
    mod.OFFSHORE_GEN_BLD_YRS = Set(
        dimen=2,
        initialize=mod.NEW_GEN_BLD_YRS,
        filter=lambda m, g, p: (
                g in m.OFFSHORE_PROJECTS))
    
    def initialize_hybrid_gen_bld_yrs_rule(mod):
        return ((s,bld_yr) for s in mod.OFFSHORE_SITES for bld_yr in mod.BLD_YRS_FOR_GEN[mod.s_to_w_map[s]])
    mod.HYBRID_GEN_BLD_YRS = Set(
        dimen=2,
        initialize=initialize_hybrid_gen_bld_yrs_rule)

    mod.GEN_TPS_HYB = Set(
    	within=mod.GEN_TPS,
        dimen=2,
        initialize=lambda m: (
            (g, tp)
                for g in m.OFFSHORE_PROJECTS
                    for tp in m.TPS_FOR_GEN[g]))
    
    mod.GENS_FOR_ZONE_TPS_HYB = Set(
        mod.LOAD_ZONES, mod.TIMEPOINTS,
        within=mod.GENS_FOR_ZONE_TPS,
        ordered=False,
        initialize=lambda m, z, t: set(g for g in m.GENS_IN_ZONE[z] if (g, t) in m.GEN_TPS_HYB))
    
    mod.GEN_PERIODS_HYBRID = Set(
        dimen=2,
        within=mod.GEN_PERIODS,
        initialize=lambda m:
            [(g, p) for g in m.OFFSHORE_PROJECTS for p in m.PERIODS_FOR_GEN[g]])
    
    mod.GENERATION_TECHNOLOGIES_HYBRID = Set(ordered=False, initialize=lambda m:
                                      {m.gen_tech[g] for g in m.OFFSHORE_PROJECTS})

    mod.GEN_TECH_PER_PERIOD_HYBRID = Set(
        initialize=lambda m: m.GENERATION_TECHNOLOGIES_HYBRID * m.PERIODS,
        dimen=2,
        within=mod.GEN_TECH_PER_PERIOD,
        doc="Set of generation technologies and periods for hybrid techs")

    def GENS_BY_TECHNOLOGY_HYBRID_init(m, t):
        if not hasattr(m, 'GENS_BY_TECH_dict_hyb'):
            m.GENS_BY_TECH_dict_hyb = {_t: [] for _t in m.GENERATION_TECHNOLOGIES_HYBRID}
            for g in m.OFFSHORE_PROJECTS:
                m.GENS_BY_TECH_dict_hyb[m.gen_tech[g]].append(g)
        result = m.GENS_BY_TECH_dict_hyb.pop(t)
        if not m.GENS_BY_TECH_dict_hyb:
            del m.GENS_BY_TECH_dict_hyb
        return result
    mod.GENS_BY_TECHNOLOGY_HYBRID = Set(
        mod.GENERATION_TECHNOLOGIES_HYBRID,
        initialize=GENS_BY_TECHNOLOGY_HYBRID_init)
    
    mod.gen_max_commit_fraction_hybrid = Param(
        mod.GEN_TPS_HYB,
        within=PercentFraction,
        default=lambda m, g, t: 1.0)
    
    mod.gen_min_load_fraction_hybrid = Param(
        mod.OFFSHORE_PROJECTS,
        within=PercentFraction,
        default=lambda m, g: 0.0)
    
    mod.gen_min_load_fraction_TP_hybrid = Param(
        mod.GEN_TPS_HYB,
        default=lambda m, g, t: m.gen_min_load_fraction_hybrid[g])
    
    mod.gen_capital_cost_annual_hybrid = Param(
        mod.OFFSHORE_GEN_BLD_YRS,
        initialize=lambda m, g, bld_yr: (
            (m.gen_overnight_cost[g, bld_yr]*m.overnight_discount) *
            crf(m.interest_rate, m.gen_max_age[g])))

    #Defining decision variables  
    mod.Built = Var(
        mod.OFFSHORE_GEN_BLD_YRS,
        within=Binary)
    
    mod.BuiltHybrid = Var(
        mod.HYBRID_GEN_BLD_YRS,
        within=Binary)
    
    mod.BuildGenHybrid = Var(
        mod.OFFSHORE_GEN_BLD_YRS,
        within=NonNegativeReals)

    mod.DispatchGenHybrid = Var(
        mod.GEN_TPS_HYB,
        within=NonNegativeReals)
    
    mod.CommitGenHybrid = Var(
        mod.GEN_TPS_HYB,
        within=NonNegativeReals)
    
    #Defining expressions
    mod.GenCapacityHybrid = Expression(
        mod.OFFSHORE_PROJECTS, mod.PERIODS,
        rule=lambda m, g, period: sum(
            m.BuildGenHybrid[g, bld_yr]
            for bld_yr in m.BLD_YRS_FOR_GEN_PERIOD[g, period]))
    
    mod.GenCapacityInTPHybrid = Expression(
        mod.GEN_TPS_HYB,
        rule=lambda m, g, t: m.GenCapacityHybrid[g, m.tp_period[t]])
    
    mod.CommitUpperLimitHybrid = Expression(
        mod.GEN_TPS_HYB,
        rule=lambda m, g, t: (
            m.GenCapacityInTPHybrid[g, t] * m.gen_availability[g] *
            m.gen_max_commit_fraction_hybrid[g, t]))
    
    mod.CommitSlackUpHybrid = Expression(
        mod.GEN_TPS_HYB,
        rule=lambda m, g, t: (
            m.CommitUpperLimitHybrid[g, t] - m.CommitGenHybrid[g, t]))
    
    mod.DispatchUpperLimitHybrid = Expression(
        mod.GEN_TPS_HYB,
        rule=lambda m, g, t: (
            m.CommitGenHybrid[g, t]*m.gen_max_capacity_factor[g, t]))
    
    mod.DispatchLowerLimitHybrid = Expression(
        mod.GEN_TPS_HYB,
        rule=lambda m, g, t: (
            m.CommitGenHybrid[g, t] * m.gen_min_load_fraction_TP_hybrid[g, t]))

    mod.GenCapitalCostsHybrid = Expression(
        mod.OFFSHORE_PROJECTS, mod.PERIODS,
        rule=lambda m, g, p: sum(
            m.BuildGenHybrid[g, bld_yr] * m.gen_capital_cost_annual_hybrid[g, bld_yr]
            for bld_yr in m.BLD_YRS_FOR_GEN_PERIOD[g, p]))
    
    mod.GenFixedOMCostsHybrid = Expression(
        mod.OFFSHORE_PROJECTS, mod.PERIODS,
        rule=lambda m, g, p: sum(
            m.BuildGenHybrid[g, bld_yr] * m.gen_fixed_om[g, bld_yr] * m.om_discount
            for bld_yr in m.BLD_YRS_FOR_GEN_PERIOD[g, p]))

    mod.TotalGenFixedCostsHybrid = Expression(
        mod.PERIODS,
        rule=lambda m, p: sum(
            m.GenCapitalCostsHybrid[g, p] + m.GenFixedOMCostsHybrid[g, p]
            for g in m.OFFSHORE_PROJECTS))
    mod.Cost_Components_Per_Period.append('TotalGenFixedCostsHybrid')

    mod.ZoneTotalCentralDispatchHybrid = Expression(
        mod.LOAD_ZONES, mod.TIMEPOINTS,
        rule=lambda m, z, t: \
        sum(m.DispatchGenHybrid[g, t]
            for g in m.GENS_FOR_ZONE_TPS_HYB[z, t]),
        doc="Net power from grid-tied hybrid wave-wind generation projects.")
    mod.Zone_Power_Injections.append('ZoneTotalCentralDispatchHybrid')

    mod.GenVariableOMCostsInTPHybrid = Expression(
        mod.TIMEPOINTS,
        rule=lambda m, t: sum(
            m.DispatchGenHybrid[g, t] * m.gen_variable_om[g] * m.om_discount
            for g in m.GENS_IN_PERIOD[m.tp_period[t]] if g in m.OFFSHORE_PROJECTS),
        doc="Summarize hybrid wave-wind project variable O&M costs for the objective function")
    mod.Cost_Components_Per_TP.append('GenVariableOMCostsInTPHybrid')

    mod.GenCapacityPerTechHybrid = Expression(
        mod.GEN_TECH_PER_PERIOD_HYBRID,
        rule=lambda m, tech, p: sum(m.GenCapacityHybrid[g, p] for g in m.GENS_BY_TECHNOLOGY_HYBRID[tech]),
        doc="The amount of power capacity for a period and technology.")

    #Defining constraints
    # mod.Enforce_Min_Build_Offshore = Constraint(
#         mod.OFFSHORE_GEN_BLD_YRS,
#         rule=lambda m, g, p: (
#             m.min_cap_mw_offshore * m.Built[g, p]
#             <= m.BuildGen[g, p]))
    
    max_build_potential_scaling_factor = 1e-1
    mod.max_build_potential_offshore = Constraint(
        mod.OFFSHORE_PROJECTS, mod.PERIODS,
        rule=lambda m, g, p: (
                m.gen_capacity_limit_mw[g] * m.Built[g, p] * max_build_potential_scaling_factor 
                >= m.GenCapacity[g, p] * max_build_potential_scaling_factor))
    
    mod.Enforce_Min_Build_Hybrid = Constraint(
        mod.OFFSHORE_GEN_BLD_YRS,
        rule=lambda m, g, p: (
            m.g_to_min_cap_map[g] * m.BuiltHybrid[m.wave_osw_site_map[g], p]
            <= m.BuildGenHybrid[g, p]))
    
    mod.max_build_potential_hybrid = Constraint(
        mod.OFFSHORE_PROJECTS, mod.PERIODS,
        rule=lambda m, g, p: (
                m.gen_capacity_limit_mw[g] * m.BuiltHybrid[m.wave_osw_site_map[g], p] * max_build_potential_scaling_factor 
                >= m.GenCapacityHybrid[g, p] * max_build_potential_scaling_factor))
    
    mod.MutallyExclusive = Constraint(
        mod.OFFSHORE_SITES, mod.PERIODS,
        rule=lambda m, s, p: 
            m.Built[m.s_to_w_map[s], p] + m.Built[m.s_to_o_map[s], p] + m.BuiltHybrid[s, p] <= 1)
    
    mod.Enforce_Commit_Upper_Limit_Hybrid = Constraint(
        mod.GEN_TPS_HYB,
        rule=lambda m, g, t: (
            m.CommitGenHybrid[g, t] <= m.CommitUpperLimitHybrid[g, t]))
    
    mod.Enforce_Dispatch_Upper_Limit_Hybrid = Constraint(
        mod.GEN_TPS_HYB,
        rule=lambda m, g, t: (
            m.DispatchGenHybrid[g, t] <= m.CommitGenHybrid[g, t]*m.gen_max_capacity_factor[g, t]))
    
    mod.DispatchSlackUpHybrid = Expression(
        mod.GEN_TPS_HYB,
        rule=lambda m, g, t: (
            m.DispatchUpperLimitHybrid[g, t] - m.DispatchGenHybrid[g, t]))
    
    mod.DispatchSlackDownHybrid = Expression(
        mod.GEN_TPS_HYB,
        rule=lambda m, g, t: (
            m.DispatchGenHybrid[g, t] - m.DispatchLowerLimitHybrid[g, t]))
    
#Post-solve output files

def post_solve(m, outdir):
	write_table(
		m,
		m.GEN_PERIODS_HYBRID,
		output_file=os.path.join(outdir, "gen_cap_hybrid.csv"),
		headings=(
			"GENERATION_PROJECT", "PERIOD",
			"gen_tech", "gen_load_zone", "gen_energy_source",
			"GenCapacity", "GenCapitalCosts", "GenFixedOMCosts"),
		# Indexes are provided as a tuple, so put (g,p) in parentheses to
		# access the two components of the index individually.
		values=lambda m, g, p: (
			g, p,
			m.gen_tech[g], m.gen_load_zone[g], m.gen_energy_source[g],
			m.GenCapacityHybrid[g, p], m.GenCapitalCostsHybrid[g, p], m.GenFixedOMCostsHybrid[g, p]))

def post_solve(mod, outdir):
	write_table(
		mod,
		mod.GEN_TECH_PER_PERIOD_HYBRID,
		output_file=os.path.join(outdir, "gen_cap_per_tech_hybrid.csv"),
		headings=(
			"gen_tech", "period", "gen_capacity"),
		values=lambda m, tech, p: (
			tech, p, m.GenCapacityPerTechHybrid[tech, p]))

    # #TO-DO:
    # #Debug Output files