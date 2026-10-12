#!/usr/bin/env python3
"""Aggregate results/*.csv into LaTeX tables (report/tables) and PDF figures (report/figures).

	python3 bench/analyze.py
	python3 bench/analyze.py --results /path/to/results --out /path/to/report
Reports mean +/- sample standard deviation (ddof=1) over repeated runs; warm-up rounds are dropped.
"""
import argparse
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
LABEL = {"vm": "VM (QEMU/KVM)", "ctr-ubuntu": "Container (ubuntu:24.04)",
		"ctr-slim": "Container (python:3.12-slim)"}
SHORT = {"vm": "VM", "ctr-ubuntu": "Ctr-Ubuntu", "ctr-slim": "Ctr-Slim"}
ORDER = ["vm", "ctr-ubuntu", "ctr-slim"]


def agg(df, col):
	g = df.groupby("target")[col].agg(["mean", "std", "count"]).reindex(ORDER).dropna(subset=["mean"])
	g["std"] = g["std"].fillna(0.0)
	return g


def pm(g, t, digits=1):
	if t not in g.index:
		return "--"
	return f"{g.loc[t, 'mean']:.{digits}f} $\\pm$ {g.loc[t, 'std']:.{digits}f}"


def write_table(path, header, rows):
	cols = "|l|" + "c|" * (len(header) - 1)
	lines = [f"\\begin{{tabular}}{{{cols}}}", "\\hline",
			" & ".join(f"\\textbf{{{h}}}" for h in header) + " \\\\", "\\hline"]
	for r in rows:
		lines += [" & ".join(r) + " \\\\", "\\hline"]
	lines.append("\\end{tabular}")
	path.write_text("\n".join(lines) + "\n")


def bar(ax, g, ylabel, log=False, title=None):
	xs = range(len(g))
	ax.bar(xs, g["mean"], yerr=g["std"], capsize=4)
	ax.set_xticks(list(xs))
	ax.set_xticklabels([SHORT[t] for t in g.index])
	ax.set_ylabel(ylabel)
	if log:
		ax.set_yscale("log")
	if title:
		ax.set_title(title, fontsize=9)
	ax.grid(axis="y", alpha=0.3)


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--results", default=str(ROOT / "results"))
	ap.add_argument("--out", default=str(ROOT / "report"))
	a = ap.parse_args()
	res, out = pathlib.Path(a.results), pathlib.Path(a.out)
	(out / "tables").mkdir(parents=True, exist_ok=True)
	(out / "figures").mkdir(parents=True, exist_ok=True)
	summary = []

	# ---- startup + idle memory
	if (res / "startup.csv").exists():
		s = pd.read_csv(res / "startup.csv")
		s = s[s["warmup"] == 0]
		g_t, g_h, g_g = agg(s, "startup_s"), agg(s, "mem_host_mb"), agg(s, "mem_guest_mb")
		rows = [[SHORT[t], pm(g_t, t, 2), pm(g_h, t), pm(g_g, t), str(int(g_t.loc[t, "count"]))]
				for t in g_t.index]
		write_table(out / "tables" / "tab_startup.tex",
					["Target", "Ready (s)", "Host mem. (MB)", "Guest mem. (MB)", "n"], rows)
		fig, ax = plt.subplots(figsize=(3.4, 2.4))
		bar(ax, g_t, "Time to first HTTP 200 (s, log scale)", log=True)
		fig.tight_layout()
		fig.savefig(out / "figures" / "fig_startup.pdf")
		plt.close(fig)
		for name, g in [("startup_s", g_t), ("idle_host_mb", g_h), ("idle_guest_mb", g_g)]:
			for t in g.index:
				summary.append({"metric": name, "target": t, "mean": g.loc[t, "mean"], "std": g.loc[t, "std"]})

	# ---- sizes
	if (res / "sizes.csv").exists():
		z = pd.read_csv(res / "sizes.csv")
		rows = [[SHORT[r.target], f"{r.size_mb:.0f}"] for r in z.itertuples() if r.target in LABEL]
		write_table(out / "tables" / "tab_size.tex", ["Target", "Size on disk (MB)"], rows)

	# ---- load tests
	if (res / "load.csv").exists():
		l = pd.read_csv(res / "load.csv")
		sat, rate = l[l["mode"] == "saturation"], l[l["mode"] == "rate"]
		if len(sat):
			if "workload" not in sat.columns:
				sat = sat.assign(workload="mixed")
			wls = list(sat["workload"].dropna().unique())
			for wl in wls:
				d = sat[sat["workload"] == wl]
				g_r, g_m, g_p, g_pk = agg(d, "rps"), agg(d, "med_ms"), agg(d, "p95_ms"), agg(d, "mem_peak_host_mb")
				rows = [[SHORT[t], pm(g_r, t, 0), pm(g_m, t), pm(g_p, t), pm(g_pk, t, 0),
						str(int(g_r.loc[t, "count"]))] for t in g_r.index]
				write_table(out / "tables" / f"tab_sat_{wl}.tex",
							["Target", "Req/s", "Median (ms)", "p95 (ms)", "Peak mem. (MB)", "n"], rows)
				for t in g_r.index:
					summary.append({"metric": f"sat_rps_{wl}", "target": t,
									"mean": g_r.loc[t, "mean"], "std": g_r.loc[t, "std"]})
					summary.append({"metric": f"sat_p95_ms_{wl}", "target": t,
									"mean": g_p.loc[t, "mean"], "std": g_p.loc[t, "std"]})
			fig, axes = plt.subplots(1, len(wls), figsize=(3.4 if len(wls) == 1 else 3.6, 2.4), squeeze=False)
			for ax, wl in zip(axes[0], wls):
				bar(ax, agg(sat[sat["workload"] == wl], "rps"),
					"Throughput (requests/s)" if ax is axes[0][0] else "", title=wl)
			fig.tight_layout()
			fig.savefig(out / "figures" / "fig_load.pdf")
			plt.close(fig)
		if len(rate):
			g_m, g_p, g_d = agg(rate, "med_ms"), agg(rate, "p95_ms"), agg(rate, "dropped")
			rows = [[SHORT[t], pm(g_m, t, 2), pm(g_p, t, 2), pm(g_d, t, 0), str(int(g_m.loc[t, "count"]))]
					for t in g_m.index]
			write_table(out / "tables" / "tab_rate.tex",
						["Target", "Median (ms)", "p95 (ms)", "Dropped iter.", "n"], rows)

	pd.DataFrame(summary).to_csv(res / "summary.csv", index=False)
	print("tables:", sorted(p.name for p in (out / "tables").glob("*.tex")))
	print("figures:", sorted(p.name for p in (out / "figures").glob("*.pdf")))


if __name__ == "__main__":
	main()
