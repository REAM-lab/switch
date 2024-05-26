import os
from matplotlib import pyplot as plt
import matplotlib

from pathlib import Path


from switch_model.tools.graph.main import Scenario

# rel_path_base = "../switch_runs/ldes_runs"
# working_dir = Path("./papers/Martin_Staadecker_et_al_2022")

rel_path_base = "../../../switch_runs/ldes_runs"
working_dir = Path("./../../papers/Martin_Staadecker_et_al_2022")


def save_figure(filename):
    plt.savefig(working_dir / "figures" / filename)


def save_df(df, csv_name):
    df.to_csv(working_dir / "figure_data" / csv_name)


def get_scenario(rel_path, name=None):
    return Scenario(os.path.join(rel_path_base, rel_path), name=name)


def set_style():
    # import matplotlib.font_manager as fm
    # fm.fontManager.addfont(str(working_dir / "fonts" / "Mona-Sans.ttf"))

    plt.interactive(True)

    plt.rcParams.update(
        {
            # Values that are slightly smaller than normal since it's a paper. Inspired from seaborn.
            "axes.linewidth": 0,
            "grid.linewidth": 0.8,
            "patch.linewidth": 0.8,
            "xtick.major.width": 0.5,
            "ytick.major.width": 0.5,
            "xtick.minor.width": 0.5,
            "ytick.minor.width": 0.5,
            # Fonts that are slightly smaller than normal since it's a paper. Follow guidelines.
            "font.size": 7,
            "axes.labelsize": 7,
            "axes.titlesize": 7,
            "xtick.labelsize": 6,
            "ytick.labelsize": 6,
            "legend.fontsize": 6,
            "legend.title_fontsize": 6,
            # Other values inspired by seaborn
            "figure.facecolor": "white",
            "axes.labelcolor": ".15",
            "xtick.direction": "out",
            "ytick.direction": "out",
            "xtick.color": ".15",
            "ytick.color": ".15",
            "axes.axisbelow": True,
            "grid.linestyle": "-",
            "text.color": ".15",
            "font.family": "arial",
            # "font.sans-serif": ["FreeSans"],
            "lines.solid_capstyle": "round",
            "patch.edgecolor": "none",
            "patch.force_edgecolor": True,
            "image.cmap": "rocket",
            "xtick.top": False,
            "ytick.right": False,
            "axes.grid": True,
            "axes.facecolor": "#EAEAF2",
            "axes.edgecolor": "white",
            "grid.color": "white",
            "axes.spines.left": True,
            "axes.spines.bottom": True,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "xtick.bottom": True,
            "ytick.left": True,
            # Custom set values
            "figure.dpi": 1000,
            "savefig.dpi": 1000,
            "figure.figsize": (7.086614, 7.086614 / 2),
            # Width according to Joule guidelines https://www.cell.com/figureguidelines
            "lines.linewidth": 1,
            "lines.markersize": 3,
            "xtick.minor.visible": False,
            "ytick.minor.visible": False,
            "xtick.major.size": 3,
            "ytick.major.size": 3,
            "xtick.minor.size": 2,
            "ytick.minor.size": 2,
            "legend.labelspacing": 0.25,
            "legend.columnspacing": 1,
            "svg.fonttype": 'none',
            # "text.antialiased": False,
        }
    )


def get_set_e_scenarios():
    return [
        get_scenario("1342", name=1.94),
        get_scenario("M7", name=2),
        get_scenario("M10", name=2.5),
        get_scenario("M9", name=3),
        get_scenario("M6", name=4),
        get_scenario("M5", name=8),
        get_scenario("M11", name=12),
        get_scenario("M4", name=16),
        get_scenario("M14", name=18),
        get_scenario("M13", name=20),
        get_scenario("M8", name=24),
        get_scenario("M3", name=32),
        get_scenario("M12", name=48),
        get_scenario("M2", name=64),
    ]
