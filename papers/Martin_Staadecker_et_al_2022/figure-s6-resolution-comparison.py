from papers.Martin_Staadecker_et_al_2022.util import (
    set_style,
    get_scenario,
    save_figure
)
from switch_model.tools.graph.main import GraphTools
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

tools = GraphTools(
    scenarios=[
        get_scenario("S1", name="1-hour resolution (Sensitivity)"),
        get_scenario("1342", name="4-hour resolution (Baseline)")
    ],
    set_style=False,
)
tools.pre_graphing(multi_scenario=True)



df = tools.get_dataframe('dispatch.csv')

# %%

# Convert to GW
df["DispatchGen_MW"] /= 1e3
# Plot Dispatch
# Add the technology type column and filter out unneeded columns
df = tools.transform.gen_type(df)
# Keep only important columns
df = df[["gen_type", "timestamp", "DispatchGen_MW", "scenario_name"]]
# Sum the values for all technology types and timepoints
df = df.groupby(["gen_type", "timestamp", "scenario_name"], as_index=False).sum()
# Add the columns time_row and time_column
df = tools.transform.timestamp(df)
df = df[["gen_type", "hour", "DispatchGen_MW", "scenario_name"]]
# Sum across all technologies that are in the same hour and scenario
df = df.groupby(["hour", "gen_type", "scenario_name"], as_index=False).mean()
# Plot curtailment

# Wrap around to include last hour as first
df_first_hour = df.loc[df["hour"] == 0]
df_first_hour["hour"] = 24
df = pd.concat([df, df_first_hour])

# %%
# Get rows
# Count number of rows and number of columns
columns = df["scenario_name"].drop_duplicates()
set_style()
fig = plt.figure()
ax1 = fig.add_subplot(1, 2, 1)
ax2 = fig.add_subplot(1, 2, 2, sharey=ax1)

ax1.set_ylabel("Average daily dispatch (GW)")

# Sort the technologies by standard deviation to have the smoothest ones at the bottom of the stacked area plot
df_all = df.pivot_table(index='hour', columns='gen_type', values="DispatchGen_MW", aggfunc=np.sum)
ordered_columns = df_all.std().sort_values().index

legend = {}

for i, (ax, column) in enumerate(zip((ax1, ax2), columns)):
    # get the dispatch for that quarter
    sub_df = df.loc[df["scenario_name"] == column]
    ax.set_title(column)
    ax.text(0, 1.025, chr(i + ord('a')), weight="bold", transform=ax.transAxes, horizontalalignment='left', verticalalignment='bottom')
    # Make it into a proper dataframe
    sub_df = sub_df.pivot(index='hour', columns='gen_type', values="DispatchGen_MW")
    sub_df = sub_df.reindex(columns=ordered_columns)
    # # Fill hours with no data with zero so x-axis doesn't skip hours
    # all_hours = tools.np.arange(0, 24, 1)
    # missing_hours = all_hours[~tools.np.isin(all_hours, sub_df.index)]
    # sub_df = sub_df.append(tools.pd.DataFrame(index=missing_hours)).sort_index().fillna(0)
    # Get axes
    # Rename to make legend proper
    sub_df = sub_df.rename_axis("Type", axis='columns')
    sub_df.plot.area(ax=ax, stacked=True, color=tools.get_colors(), legend=False)
    ax.set_xlabel("Time of day (PST)")
    # Get all the legend labels and add them to legend dictionary.
    # Since it's a dictionary, duplicates are dropped
    handles, labels = ax.get_legend_handles_labels()
    for i in range(len(handles)):
        legend[labels[i]] = handles[i]

ax1.set_xticks(list(range(25)))
ax2.set_xticks(list(range(0, 25, 4)))
ax2.tick_params(axis='y', which="both", left=False)

# Remove space between subplot columns
# plt.subplots_adjust(wspace=0.5)
plt.tight_layout()
# Add the legend
legend_pairs = legend.items()
fig.legend([h for _, h in legend_pairs], [l for l, _ in legend_pairs])

save_figure("figure-s6-resolution-comparison.png")
