#  ___________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright 2017 National Technology and Engineering Solutions of Sandia, LLC
#  Under the terms of Contract DE-NA0003525 with National Technology and 
#  Engineering Solutions of Sandia, LLC, the U.S. Government retains certain 
#  rights in this software.
#  This software is distributed under the 3-clause BSD License.
#  ___________________________________________________________________________

import pyomo.util.plugin
from pyomo.pysp import solutionwriter
from pyomo.pysp.scenariotree.tree_structure import \
    ScenarioTree

import switch_model.utilities
import os

#
# a simple utility to munge the index name into something a
# bit more csv-friendly and in general more readable. at the
# current time, we just eliminate any leading and trailing
# parentheses and change commas to colons - the latter
# because it's a csv file!
#

def index_to_string(index):

    result = str(index)
    result = result.lstrip('(').rstrip(')')
    result = result.replace(',',':')
    result = result.replace(' ','')

    return result


def write_csv_soln(scenario_tree, output_file_prefix):
    """
    Write the csv solution to a file.
    Args: scenario_tree: a scenario tree object populated with a solution.
          output_file_prefix: a string to indicate the file names for output.
                              output_file_prefix + ".csv"
                              output_file_prefix + "_StageCostDetail.csv"
    """
	
#	import ipdb; ipdb.set_trace()
# josiah's sample code Find the actual syntax/API hooks to use in these next lines
#	for scenario in scenario_tree.scenarios:
#		scenario_instance = scenario_tree.load_one_scenario(scenario)
#	for scenario_instance in scenario_tree._scenarios:
#		switch_model.utilities.post_solve(scenario_instance, outputs_dir=os.path.join('outputs', scenario.name))
# end of josiah's sample code

    if isinstance(scenario_tree, ScenarioTree):
    	print("Yay.")
    	for scenario_instance in scenario_tree._scenarios:
			switch_model.utilities.post_solve(scenario_instance._instance, outputs_dir=os.path.join('outputs', scenario_instance.name))
    if not isinstance(scenario_tree, ScenarioTree):
        raise RuntimeError(
            "SwitchSolutionWriter write method expects "
            "ScenarioTree object - type of supplied "
            "object="+str(type(scenario_tree)))

    solution_filename = output_file_prefix + ".switch.csv"
    with open(solution_filename, "w") as f:
        for stage in scenario_tree.stages:
            for tree_node in sorted(stage.nodes,
                                    key=lambda x: x.name):
                for variable_id in sorted(tree_node._variable_ids):
                    var_name, index = \
                        tree_node._variable_ids[variable_id]
                    f.write("%s, %s, %s, %s, %s\n"
                            % (stage.name,
                               tree_node.name,
                               var_name,
                               index_to_string(index),
                               tree_node._solution[variable_id]))

    print("Scenario tree solution written to file="+solution_filename)

    cost_filename = output_file_prefix + "_StageCostDetail.csv"
    with open(cost_filename, "w") as f:
        for stage in scenario_tree.stages:
            cost_name, cost_index = stage._cost_variable
            for tree_node in sorted(stage.nodes,
                                    key=lambda x: x.name):
                for scenario in sorted(tree_node.scenarios,
                                       key=lambda x: x.name):
                    stage_cost = scenario._stage_costs[stage.name]
                    f.write("%s, %s, %s, %s, %s, %s\n"
                            % (stage.name,
                               tree_node.name,
                               scenario.name,
                               cost_name,
                               index_to_string(cost_index),
                               stage_cost))
    print("Scenario stage costs written to file="+cost_filename)


class SwitchSolutionWriter(pyomo.util.plugin.SingletonPlugin):

    pyomo.util.plugin.implements(
        solutionwriter.ISolutionWriterExtension)

    def write(self, scenario_tree, output_file_prefix):
        write_csv_soln(scenario_tree, output_file_prefix)

