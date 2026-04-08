from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import difflib

import numpy as np
import pandas as pd
import statsmodels.api as sm
from linearmodels import PanelOLS
from linearmodels.iv import IV2SLS


@dataclass
class AnalysisSpec:
    data: str
    outcome: str
    treatment: str
    controls: list[str] = field(default_factory=list)
    query: str = ""
    instrument: str | None = None
    entity_id: str | None = None
    time_id: str | None = None
    model: str = "auto"
    save_summary: str | None = None


class RulePlanner:
    IV_KEYWORDS = {"iv", "2sls", "instrument", "instrumental", "endogeneity", "endogenous"}
    FE_KEYWORDS = {"fe", "fixed effect", "fixed effects", "twfe", "panel", "within", "entity effect", "time effect"}

    @classmethod
    def choose_model(cls, spec: AnalysisSpec, df: pd.DataFrame) -> tuple[str, list[str]]:
        reasons: list[str] = []
        query = (spec.query or "").lower()

        if spec.model != "auto":
            return spec.model, [f"user_forced_model={spec.model}"]

        if spec.instrument:
            reasons.append("instrument was provided explicitly")
            return "iv", reasons

        if any(keyword in query for keyword in cls.IV_KEYWORDS):
            reasons.append("query mentions IV / endogenous treatment")
            return "iv", reasons

        if spec.entity_id and spec.time_id:
            repeated_entities = df[spec.entity_id].nunique() < len(df)
            if repeated_entities:
                reasons.append("entity_id and time_id indicate panel structure")
            if repeated_entities or any(keyword in query for keyword in cls.FE_KEYWORDS):
                reasons.append("panel data defaults to FE as the local baseline")
                return "fe", reasons

        reasons.append("fallback baseline is robust OLS")
        return "ols", reasons

    @staticmethod
    def build_plan(model: str) -> list[str]:
        steps = [
            "load dataset and validate requested variables",
            "coerce selected analysis columns to numeric and drop unusable rows",
            f"fit the {model.upper()} model with the lightweight local tool library",
            "run reflection on failures or weak empirical diagnostics",
            "print a compact, explainable summary",
        ]
        return steps


class EconometricTools:
    @staticmethod
    def fit_ols(df: pd.DataFrame, spec: AnalysisSpec):
        X = sm.add_constant(df[[spec.treatment] + spec.controls], has_constant="add")
        y = df[spec.outcome]
        return sm.OLS(y, X).fit(cov_type="HC1")

    @staticmethod
    def fit_fe(df: pd.DataFrame, spec: AnalysisSpec):
        if not spec.entity_id or not spec.time_id:
            raise ValueError("FE requires both --entity-id and --time-id.")
        panel = df.set_index([spec.entity_id, spec.time_id]).sort_index()
        X = panel[[spec.treatment] + spec.controls]
        y = panel[spec.outcome]
        model = PanelOLS(y, X, entity_effects=True, time_effects=True, drop_absorbed=True)
        return model.fit(cov_type="clustered", cluster_entity=True)

    @staticmethod
    def fit_iv(df: pd.DataFrame, spec: AnalysisSpec):
        if not spec.instrument:
            raise ValueError("IV requires --instrument.")
        y = df[spec.outcome]
        exog = pd.DataFrame(index=df.index)
        exog["const"] = 1.0
        for col in spec.controls:
            exog[col] = df[col]
        endog = df[spec.treatment]
        instruments = df[[spec.instrument]]
        return IV2SLS(y, exog, endog, instruments).fit(cov_type="robust")


class LiteEconometricsAgent:
    def __init__(self, spec: AnalysisSpec):
        self.spec = spec
        self.reflection_log: list[str] = []

    def run(self) -> dict[str, Any]:
        raw = self._load_data(self.spec.data)
        model, reasons = RulePlanner.choose_model(self.spec, raw)
        plan = RulePlanner.build_plan(model)
        prepared = self._prepare_data(raw, model)
        result = self._fit_model(prepared, model)
        diagnostics = self._evaluate_result(prepared, model, result)
        summary = self._build_summary(prepared, model, reasons, plan, result, diagnostics)
        self._emit(summary)
        if self.spec.save_summary:
            Path(self.spec.save_summary).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        return summary

    def _load_data(self, path_str: str) -> pd.DataFrame:
        path = Path(path_str)
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return pd.read_csv(path)
        if suffix == ".parquet":
            return pd.read_parquet(path)
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(path)
        raise ValueError(f"Unsupported data format: {suffix}. Use csv, parquet, or xlsx.")

    def _prepare_data(self, df: pd.DataFrame, model: str) -> pd.DataFrame:
        frame = df.copy()
        stripped = {col: str(col).strip() for col in frame.columns}
        if list(stripped.values()) != list(frame.columns):
            self.reflection_log.append("trimmed whitespace from column names before matching variables")
        frame = frame.rename(columns=stripped)

        resolved = {}
        for field_name in ["outcome", "treatment", "instrument", "entity_id", "time_id"]:
            value = getattr(self.spec, field_name)
            if value:
                resolved[field_name] = self._resolve_column(frame, value)
        resolved_controls = [self._resolve_column(frame, col) for col in self.spec.controls]

        self.spec.outcome = resolved["outcome"]
        self.spec.treatment = resolved["treatment"]
        self.spec.controls = resolved_controls
        self.spec.instrument = resolved.get("instrument")
        self.spec.entity_id = resolved.get("entity_id")
        self.spec.time_id = resolved.get("time_id")

        numeric_cols = [self.spec.outcome, self.spec.treatment] + self.spec.controls
        if self.spec.instrument:
            numeric_cols.append(self.spec.instrument)
        for col in numeric_cols:
            before_na = frame[col].isna().sum()
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
            after_na = frame[col].isna().sum()
            if after_na > before_na:
                self.reflection_log.append(f"coerced {col} to numeric and introduced {after_na - before_na} NaNs")

        required = [self.spec.outcome, self.spec.treatment] + self.spec.controls
        if self.spec.instrument:
            required.append(self.spec.instrument)
        if self.spec.entity_id:
            required.append(self.spec.entity_id)
        if self.spec.time_id:
            required.append(self.spec.time_id)

        before_rows = len(frame)
        frame = frame.dropna(subset=required).copy()
        dropped_rows = before_rows - len(frame)
        if dropped_rows:
            self.reflection_log.append(f"dropped {dropped_rows} rows with missing required variables")

        dropped_controls = []
        for col in list(self.spec.controls):
            if frame[col].nunique(dropna=True) <= 1:
                dropped_controls.append(col)
                self.spec.controls.remove(col)
        if dropped_controls:
            self.reflection_log.append(f"removed constant controls: {', '.join(dropped_controls)}")

        if model == "iv" and self.spec.instrument and frame[self.spec.instrument].nunique(dropna=True) <= 1:
            raise ValueError(f"Instrument {self.spec.instrument} has no variation after cleaning.")

        return frame

    def _fit_model(self, df: pd.DataFrame, model: str):
        tools = {
            "ols": EconometricTools.fit_ols,
            "fe": EconometricTools.fit_fe,
            "iv": EconometricTools.fit_iv,
        }
        try:
            return tools[model](df, self.spec)
        except Exception as exc:
            self.reflection_log.append(f"primary fit failed: {exc}")
            raise

    def _evaluate_result(self, df: pd.DataFrame, model: str, result) -> list[str]:
        diagnostics: list[str] = []
        coef = float(result.params[self.spec.treatment])
        pvalue = float(result.pvalues[self.spec.treatment])

        if np.isnan(coef) or np.isnan(pvalue):
            diagnostics.append("main coefficient is NaN; check collinearity, absorbed effects, or missingness")
        elif pvalue > 0.1:
            diagnostics.append("main effect is imprecisely estimated at the 10% level")
        else:
            diagnostics.append("main effect is statistically informative at conventional thresholds")

        if model == "fe":
            within_variation = df.groupby(self.spec.entity_id)[self.spec.treatment].nunique().gt(1).mean()
            diagnostics.append(f"share of entities with within-unit treatment variation: {within_variation:.2f}")

        if model == "iv" and hasattr(result, "first_stage"):
            try:
                first_stage = result.first_stage.diagnostics
                if not first_stage.empty:
                    first_row = first_stage.iloc[0].to_dict()
                    f_stat = first_row.get("f.stat") or first_row.get("f")
                    if f_stat is not None:
                        diagnostics.append(f"first-stage F statistic: {float(f_stat):.2f}")
                        if float(f_stat) < 10:
                            diagnostics.append("instrument may be weak by the common F < 10 rule")
            except Exception as exc:
                diagnostics.append(f"could not parse first-stage diagnostics: {exc}")

        return diagnostics

    def _build_summary(
        self,
        df: pd.DataFrame,
        model: str,
        reasons: list[str],
        plan: list[str],
        result,
        diagnostics: list[str],
    ) -> dict[str, Any]:
        se_series = getattr(result, "std_errors", getattr(result, "bse", None))
        r2 = getattr(result, "rsquared_adj", getattr(result, "rsquared", None))
        terms = pd.DataFrame(
            {
                "term": list(result.params.index),
                "coef": [round(float(v), 6) for v in result.params.values],
                "std_error": [round(float(v), 6) for v in se_series.values],
                "pvalue": [round(float(v), 6) for v in result.pvalues.values],
            }
        )

        return {
            "selected_model": model,
            "selection_reasons": reasons,
            "plan": plan,
            "data_rows_used": int(len(df)),
            "outcome": self.spec.outcome,
            "treatment": self.spec.treatment,
            "controls": self.spec.controls,
            "instrument": self.spec.instrument,
            "main_result": {
                "coefficient": round(float(result.params[self.spec.treatment]), 6),
                "std_error": round(float(se_series[self.spec.treatment]), 6),
                "pvalue": round(float(result.pvalues[self.spec.treatment]), 6),
                "r_squared": None if r2 is None else round(float(r2), 6),
            },
            "diagnostics": diagnostics,
            "reflection": self.reflection_log,
            "all_terms": terms.to_dict(orient="records"),
        }

    def _emit(self, summary: dict[str, Any]) -> None:
        print("\n=== Lightweight Econometrics Agent ===")
        print(json.dumps({k: v for k, v in summary.items() if k != "all_terms"}, indent=2, ensure_ascii=False))
        print("\n=== Coefficient Table ===")
        print(pd.DataFrame(summary["all_terms"]).to_string(index=False))

    @staticmethod
    def _resolve_column(df: pd.DataFrame, requested: str) -> str:
        if requested in df.columns:
            return requested
        lookup = {str(col).lower(): str(col) for col in df.columns}
        lowered = requested.lower()
        if lowered in lookup:
            return lookup[lowered]
        candidates = difflib.get_close_matches(requested, list(df.columns), n=3, cutoff=0.6)
        hint = f" Did you mean: {candidates}?" if candidates else ""
        raise KeyError(f"Column '{requested}' not found.{hint}")


def make_demo_data(output_dir: Path) -> dict[str, Path]:
    rng = np.random.default_rng(42)
    output_dir.mkdir(parents=True, exist_ok=True)

    n = 300
    x = rng.normal(size=n)
    c = rng.normal(size=n)
    y = 1.5 * x + 0.7 * c + rng.normal(scale=1.0, size=n)
    ols_df = pd.DataFrame({"y": y, "x": x, "c": c})
    ols_path = output_dir / "ols_demo.csv"
    ols_df.to_csv(ols_path, index=False)

    firms = np.repeat(np.arange(40), 6)
    years = np.tile(np.arange(2015, 2021), 40)
    firm_fe = rng.normal(size=40).repeat(6)
    year_fe_map = {year: shock for year, shock in zip(range(2015, 2021), rng.normal(size=6))}
    year_fe = np.array([year_fe_map[y] for y in years])
    treat = ((firms < 20) & (years >= 2018)).astype(int)
    c_panel = rng.normal(size=len(firms))
    y_panel = 2.0 * treat + 0.5 * c_panel + firm_fe + year_fe + rng.normal(scale=0.7, size=len(firms))
    fe_df = pd.DataFrame({"firm": firms, "year": years, "y": y_panel, "treat": treat, "c": c_panel})
    fe_path = output_dir / "fe_demo.csv"
    fe_df.to_csv(fe_path, index=False)

    z = rng.normal(size=n)
    u = rng.normal(size=n)
    treat_iv = 0.9 * z + 0.6 * u + rng.normal(scale=0.5, size=n)
    y_iv = 1.8 * treat_iv + 0.5 * c + 0.8 * u + rng.normal(scale=0.8, size=n)
    iv_df = pd.DataFrame({"y": y_iv, "treat": treat_iv, "z": z, "c": c})
    iv_path = output_dir / "iv_demo.csv"
    iv_df.to_csv(iv_path, index=False)

    return {"ols": ols_path, "fe": fe_path, "iv": iv_path}


def run_demo(output_dir: str) -> None:
    paths = make_demo_data(Path(output_dir))
    demo_specs = [
        AnalysisSpec(data=str(paths["ols"]), query="baseline ols", outcome="y", treatment="x", controls=["c"], model="auto"),
        AnalysisSpec(
            data=str(paths["fe"]),
            query="estimate the policy effect with firm and year fixed effects",
            outcome="y",
            treatment="treat",
            controls=["c"],
            entity_id="firm",
            time_id="year",
            model="auto",
        ),
        AnalysisSpec(
            data=str(paths["iv"]),
            query="estimate the endogenous treatment effect with IV-2SLS",
            outcome="y",
            treatment="treat",
            controls=["c"],
            instrument="z",
            model="auto",
        ),
    ]
    for spec in demo_specs:
        LiteEconometricsAgent(spec).run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal local econometrics agent.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run one analysis task.")
    run_parser.add_argument("--data", required=True)
    run_parser.add_argument("--query", default="")
    run_parser.add_argument("--outcome", required=True)
    run_parser.add_argument("--treatment", required=True)
    run_parser.add_argument("--controls", nargs="*", default=[])
    run_parser.add_argument("--instrument")
    run_parser.add_argument("--entity-id")
    run_parser.add_argument("--time-id")
    run_parser.add_argument("--model", choices=["auto", "ols", "fe", "iv"], default="auto")
    run_parser.add_argument("--save-summary")

    demo_parser = subparsers.add_parser("demo", help="Generate demo data and run OLS/FE/IV examples.")
    demo_parser.add_argument("--output-dir", default="lite_demo_output")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "demo":
        run_demo(args.output_dir)
        return

    spec = AnalysisSpec(
        data=args.data,
        query=args.query,
        outcome=args.outcome,
        treatment=args.treatment,
        controls=args.controls,
        instrument=args.instrument,
        entity_id=args.entity_id,
        time_id=args.time_id,
        model=args.model,
        save_summary=args.save_summary,
    )
    LiteEconometricsAgent(spec).run()


if __name__ == "__main__":
    main()
