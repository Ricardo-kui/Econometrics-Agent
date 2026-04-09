from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import difflib

import numpy as np
import pandas as pd
import statsmodels.api as sm
from linearmodels import PanelOLS
from linearmodels.iv import IV2SLS

from lite_method_knowledge import METHOD_KNOWLEDGE


def is_binary_like(series: pd.Series) -> bool:
    clean = pd.Series(series).dropna()
    if clean.empty:
        return False
    numeric = pd.to_numeric(clean, errors="coerce")
    if numeric.isna().any():
        return False
    return numeric.isin([0, 1]).all()


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
    treat_group: str | None = None
    post: str | None = None
    model: str = "auto"
    lead_window: int = 4
    lag_window: int = 3
    save_summary: str | None = None


@dataclass
class FitBundle:
    result: Any
    main_term: str
    model_label: str
    extras: dict[str, Any] = field(default_factory=dict)


class RulePlanner:
    IV_KEYWORDS = {"iv", "2sls", "instrument", "instrumental", "endogeneity", "endogenous"}
    FE_KEYWORDS = {"fe", "fixed effect", "fixed effects", "twfe", "panel", "within", "entity effect", "time effect"}
    DID_KEYWORDS = {"did", "difference in difference", "difference-in-differences", "policy shock", "treated", "staggered adoption"}
    EVENT_STUDY_KEYWORDS = {"event study", "dynamic effect", "dynamic treatment", "lead", "lag", "pretrend", "pre-trend"}

    @classmethod
    def has_panel_structure(cls, spec: AnalysisSpec, df: pd.DataFrame) -> bool:
        return bool(
            spec.entity_id
            and spec.time_id
            and spec.entity_id in df.columns
            and spec.time_id in df.columns
            and df[spec.entity_id].nunique() < len(df)
        )

    @classmethod
    def choose_model(cls, spec: AnalysisSpec, df: pd.DataFrame) -> tuple[str, list[str]]:
        reasons: list[str] = []
        query = (spec.query or "").lower()
        panel = cls.has_panel_structure(spec, df)
        did_requested = any(keyword in query for keyword in cls.DID_KEYWORDS)
        event_requested = any(keyword in query for keyword in cls.EVENT_STUDY_KEYWORDS)

        if spec.model != "auto":
            return spec.model, [f"user_forced_model={spec.model}"]

        if spec.instrument:
            reasons.append("instrument was provided explicitly")
            return "iv", reasons

        if any(keyword in query for keyword in cls.IV_KEYWORDS):
            reasons.append("query mentions IV / endogenous treatment")
            return "iv", reasons

        if event_requested and panel:
            reasons.append("query asks for dynamic treatment effects or pre-trends")
            reasons.append("panel structure is available for lead-lag estimation")
            return "event-study", reasons

        if spec.treat_group and spec.post:
            reasons.append("explicit treat_group and post variables define a DID design")
            return "did", reasons

        if did_requested and panel and spec.treatment in df.columns and is_binary_like(df[spec.treatment]):
            reasons.append("query describes a policy-shock / DID design")
            reasons.append("panel treatment indicator is binary and can be used as a staggered DID regressor")
            return "did", reasons

        if panel:
            reasons.append("entity_id and time_id indicate panel structure")
            reasons.append("panel data defaults to FE as the local baseline")
            return "fe", reasons

        reasons.append("fallback baseline is robust OLS")
        return "ols", reasons

    @staticmethod
    def build_plan(model: str) -> list[str]:
        card = METHOD_KNOWLEDGE[model]
        diagnostics = ", ".join(card["diagnostics_to_check"][:3])
        steps = [
            "load dataset and validate requested variables against the econometric design",
            f"apply routing rule: {card['core_rule']}",
            f"estimate {card['display_name']} with the lightweight local tool library",
            f"check diagnostics: {diagnostics}",
            "report coefficients, identification risks, and reflection notes in a compact summary",
        ]
        return steps


class EconometricTools:
    @staticmethod
    def fit_ols(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        X = sm.add_constant(df[[spec.treatment] + spec.controls], has_constant="add")
        y = df[spec.outcome]
        result = sm.OLS(y, X).fit(cov_type="HC1")
        return FitBundle(result=result, main_term=spec.treatment, model_label="ols")

    @staticmethod
    def fit_fe(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        if not spec.entity_id or not spec.time_id:
            raise ValueError("FE requires both --entity-id and --time-id.")
        panel = df.set_index([spec.entity_id, spec.time_id]).sort_index()
        X = panel[[spec.treatment] + spec.controls]
        y = panel[spec.outcome]
        model = PanelOLS(y, X, entity_effects=True, time_effects=True, drop_absorbed=True)
        result = model.fit(cov_type="clustered", cluster_entity=True)
        return FitBundle(result=result, main_term=spec.treatment, model_label="fe")

    @staticmethod
    def fit_iv(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        if not spec.instrument:
            raise ValueError("IV requires --instrument.")
        y = df[spec.outcome]
        exog = pd.DataFrame(index=df.index)
        exog["const"] = 1.0
        for col in spec.controls:
            exog[col] = df[col]
        endog = df[spec.treatment]
        instruments = df[[spec.instrument]]
        result = IV2SLS(y, exog, endog, instruments).fit(cov_type="robust")
        return FitBundle(result=result, main_term=spec.treatment, model_label="iv")

    @staticmethod
    def fit_did(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        if spec.treat_group and spec.post:
            return EconometricTools._fit_static_did(df, spec)
        return EconometricTools._fit_staggered_did(df, spec)

    @staticmethod
    def _fit_static_did(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        interaction = (df[spec.treat_group] * df[spec.post]).rename("did")
        if spec.entity_id and spec.time_id:
            panel = df[[spec.outcome, spec.entity_id, spec.time_id] + spec.controls].copy()
            panel["did"] = interaction
            panel = panel.set_index([spec.entity_id, spec.time_id]).sort_index()
            X = panel[["did"] + spec.controls]
            y = panel[spec.outcome]
            result = PanelOLS(y, X, entity_effects=True, time_effects=True, drop_absorbed=True).fit(
                cov_type="clustered",
                cluster_entity=True,
                cluster_time=True,
            )
        else:
            X = df[[spec.treat_group, spec.post] + spec.controls].copy()
            X["did"] = interaction
            X = sm.add_constant(X[["did", spec.treat_group, spec.post] + spec.controls], has_constant="add")
            y = df[spec.outcome]
            result = sm.OLS(y, X).fit(cov_type="HC1")
        return FitBundle(
            result=result,
            main_term="did",
            model_label="did",
            extras={"design": "static", "priority_terms": ["did"]},
        )

    @staticmethod
    def _fit_staggered_did(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        if not spec.entity_id or not spec.time_id:
            raise ValueError("Staggered DID requires both --entity-id and --time-id.")
        panel = df.set_index([spec.entity_id, spec.time_id]).sort_index()
        X = panel[[spec.treatment] + spec.controls]
        y = panel[spec.outcome]
        result = PanelOLS(y, X, entity_effects=True, time_effects=True, drop_absorbed=True).fit(
            cov_type="clustered",
            cluster_entity=True,
            cluster_time=True,
        )
        return FitBundle(
            result=result,
            main_term=spec.treatment,
            model_label="did",
            extras={"design": "staggered", "priority_terms": [spec.treatment]},
        )

    @staticmethod
    def fit_event_study(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        if not spec.entity_id or not spec.time_id:
            raise ValueError("Event study requires both --entity-id and --time-id.")
        if spec.lead_window < 2 or spec.lag_window < 1:
            raise ValueError("Event study requires --lead-window >= 2 and --lag-window >= 1.")
        if not is_binary_like(df[spec.treatment]):
            raise ValueError("Event study requires a binary treatment indicator that switches on after adoption.")

        columns = [spec.outcome, spec.treatment, spec.entity_id, spec.time_id] + spec.controls
        working = df[columns].copy().sort_values([spec.entity_id, spec.time_id])

        unique_times = working[[spec.time_id]].drop_duplicates().sort_values(spec.time_id)[spec.time_id].tolist()
        time_order = {value: idx for idx, value in enumerate(unique_times)}
        working["__time_order"] = working[spec.time_id].map(time_order)

        first_treatment = working.groupby(spec.entity_id, sort=False)[spec.treatment].first()
        always_treated_entities = first_treatment[first_treatment == 1].index.tolist()
        if always_treated_entities:
            working = working[~working[spec.entity_id].isin(always_treated_entities)].copy()

        adoption_order = working.loc[working[spec.treatment] == 1].groupby(spec.entity_id)["__time_order"].min()
        working["__adoption_order"] = working[spec.entity_id].map(adoption_order)

        lead_terms = [f"lead_{spec.lead_window}_plus"] + [f"lead_{i}" for i in range(spec.lead_window - 1, 1, -1)]
        post_terms = ["event_0"] + [f"lag_{i}" for i in range(1, spec.lag_window)] + [f"lag_{spec.lag_window}_plus"]
        event_terms = lead_terms + post_terms

        for term in event_terms:
            working[term] = 0.0

        for idx, row in working.iterrows():
            adoption = row["__adoption_order"]
            if pd.isna(adoption):
                continue
            delta = int(row["__time_order"] - adoption)
            selected_term = None
            if delta <= -spec.lead_window:
                selected_term = f"lead_{spec.lead_window}_plus"
            elif -spec.lead_window < delta <= -2:
                selected_term = f"lead_{abs(delta)}"
            elif delta == 0:
                selected_term = "event_0"
            elif 1 <= delta < spec.lag_window:
                selected_term = f"lag_{delta}"
            elif delta >= spec.lag_window:
                selected_term = f"lag_{spec.lag_window}_plus"
            if selected_term:
                working.at[idx, selected_term] = 1.0

        panel = working.set_index([spec.entity_id, spec.time_id]).sort_index()
        X = panel[event_terms + spec.controls]
        y = panel[spec.outcome]
        result = PanelOLS(y, X, entity_effects=True, time_effects=True, drop_absorbed=True).fit(
            cov_type="clustered",
            cluster_entity=True,
            cluster_time=True,
        )
        return FitBundle(
            result=result,
            main_term="event_0",
            model_label="event-study",
            extras={
                "event_terms": event_terms,
                "pre_terms": lead_terms,
                "post_terms": post_terms,
                "priority_terms": ["event_0"] + [term for term in post_terms if term != "event_0"],
                "dropped_always_treated_entities": always_treated_entities,
            },
        )


class LiteEconometricsAgent:
    def __init__(self, spec: AnalysisSpec):
        self.spec = spec
        self.reflection_log: list[str] = []

    def run(self) -> dict[str, Any]:
        raw = self._strip_column_names(self._load_data(self.spec.data))
        model, reasons = RulePlanner.choose_model(self.spec, raw)
        plan = RulePlanner.build_plan(model)
        self._capture_routing_reflection(model, raw)
        prepared = self._prepare_data(raw, model)
        bundle = self._fit_model(prepared, model)
        diagnostics = self._evaluate_result(prepared, model, bundle)
        summary = self._build_summary(prepared, model, reasons, plan, bundle, diagnostics)
        self._emit(summary)
        if self.spec.save_summary:
            Path(self.spec.save_summary).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        return summary

    def _load_data(self, path_str: str) -> pd.DataFrame:
        path = Path(path_str)
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return pd.read_csv(path)
        if suffix == ".dta":
            return pd.read_stata(path, convert_categoricals=False)
        if suffix == ".parquet":
            return pd.read_parquet(path)
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(path)
        raise ValueError(f"Unsupported data format: {suffix}. Use csv, dta, parquet, or xlsx.")

    def _strip_column_names(self, df: pd.DataFrame) -> pd.DataFrame:
        frame = df.copy()
        stripped = {col: str(col).strip() for col in frame.columns}
        if list(stripped.values()) != list(frame.columns):
            self.reflection_log.append("trimmed whitespace from column names before matching variables")
            frame = frame.rename(columns=stripped)
        return frame

    def _capture_routing_reflection(self, model: str, df: pd.DataFrame) -> None:
        query = (self.spec.query or "").lower()
        did_like = any(keyword in query for keyword in RulePlanner.DID_KEYWORDS)
        if did_like and model not in {"did", "event-study"}:
            self.reflection_log.append(
                "query mentions a DID-style design, but available variables were insufficient for DID routing; review treat_group/post or panel treatment timing"
            )
        if model == "event-study" and RulePlanner.has_panel_structure(self.spec, df):
            self.reflection_log.append("event-study was selected because the task asks for dynamic treatment effects in panel data")

    def _prepare_data(self, df: pd.DataFrame, model: str) -> pd.DataFrame:
        frame = df.copy()

        resolved = {}
        for field_name in ["outcome", "treatment", "instrument", "entity_id", "time_id", "treat_group", "post"]:
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
        self.spec.treat_group = resolved.get("treat_group")
        self.spec.post = resolved.get("post")

        numeric_cols = [self.spec.outcome, self.spec.treatment] + self.spec.controls
        for optional_col in [self.spec.instrument, self.spec.treat_group, self.spec.post]:
            if optional_col:
                numeric_cols.append(optional_col)
        for col in numeric_cols:
            before_na = frame[col].isna().sum()
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
            after_na = frame[col].isna().sum()
            if after_na > before_na:
                self.reflection_log.append(f"coerced {col} to numeric and introduced {after_na - before_na} NaNs")

        required = [self.spec.outcome, self.spec.treatment] + self.spec.controls
        for optional_col in [self.spec.instrument, self.spec.treat_group, self.spec.post, self.spec.entity_id, self.spec.time_id]:
            if optional_col:
                required.append(optional_col)

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

        if model in {"did", "event-study"}:
            if self.spec.treat_group and self.spec.post:
                if not is_binary_like(frame[self.spec.treat_group]):
                    raise ValueError(f"{self.spec.treat_group} must be binary for DID.")
                if not is_binary_like(frame[self.spec.post]):
                    raise ValueError(f"{self.spec.post} must be binary for DID.")
            elif not is_binary_like(frame[self.spec.treatment]):
                raise ValueError("DID and event-study require a binary treatment indicator unless treat_group and post are provided explicitly.")

        if model in {"fe", "did", "event-study"} and self.spec.entity_id and self.spec.time_id:
            frame = frame.sort_values([self.spec.entity_id, self.spec.time_id]).copy()

        return frame

    def _fit_model(self, df: pd.DataFrame, model: str) -> FitBundle:
        tools = {
            "ols": EconometricTools.fit_ols,
            "fe": EconometricTools.fit_fe,
            "iv": EconometricTools.fit_iv,
            "did": EconometricTools.fit_did,
            "event-study": EconometricTools.fit_event_study,
        }
        try:
            bundle = tools[model](df, self.spec)
        except Exception as exc:
            self.reflection_log.append(f"primary fit failed: {exc}")
            raise

        bundle.main_term = self._resolve_report_term(bundle)
        dropped_always_treated = bundle.extras.get("dropped_always_treated_entities", [])
        if dropped_always_treated:
            self.reflection_log.append(
                f"dropped {len(dropped_always_treated)} always-treated entities before event-study estimation"
            )
        return bundle

    def _resolve_report_term(self, bundle: FitBundle) -> str:
        params_index = list(bundle.result.params.index)
        if bundle.main_term in params_index:
            return bundle.main_term
        for candidate in bundle.extras.get("priority_terms", []):
            if candidate in params_index:
                self.reflection_log.append(
                    f"main term {bundle.main_term} was absorbed or unavailable; reporting {candidate} instead"
                )
                return candidate
        raise ValueError(f"Could not find a reportable treatment term in model output: {params_index}")

    def _evaluate_result(self, df: pd.DataFrame, model: str, bundle: FitBundle) -> list[str]:
        result = bundle.result
        term = bundle.main_term
        diagnostics: list[str] = []
        coef = float(result.params[term])
        pvalue = float(result.pvalues[term])

        if np.isnan(coef) or np.isnan(pvalue):
            diagnostics.append("main coefficient is NaN; check collinearity, absorbed effects, or missingness")
        elif pvalue > 0.1:
            diagnostics.append("main effect is imprecisely estimated at the 10% level")
        else:
            diagnostics.append("main effect is statistically informative at conventional thresholds")

        if model in {"fe", "did"} and self.spec.entity_id and self.spec.entity_id in df.columns:
            within_variation = df.groupby(self.spec.entity_id)[self.spec.treatment].nunique().gt(1).mean()
            diagnostics.append(f"share of entities with within-unit treatment variation: {within_variation:.2f}")

        if model == "did":
            if bundle.extras.get("design") == "static":
                diagnostics.append("static DID still relies on a parallel-trends assumption; use event-study for explicit pre-trend inspection")
            else:
                monotone_share = df.groupby(self.spec.entity_id)[self.spec.treatment].apply(lambda s: (s.diff().fillna(0) >= 0).all()).mean()
                diagnostics.append(f"share of entities with weakly monotone treatment timing: {monotone_share:.2f}")

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

        if model == "event-study":
            pre_terms = [term_name for term_name in bundle.extras.get("pre_terms", []) if term_name in result.params.index]
            if pre_terms:
                significant_pre = int((result.pvalues[pre_terms] < 0.1).sum())
                diagnostics.append(f"significant pre-treatment lead coefficients at 10%: {significant_pre}/{len(pre_terms)}")
                if significant_pre > 0:
                    diagnostics.append("pre-trend evidence is noisy; parallel trends may be questionable")
            post_terms = [term_name for term_name in bundle.extras.get("post_terms", []) if term_name in result.params.index]
            if post_terms:
                diagnostics.append(f"available dynamic post-treatment coefficients: {', '.join(post_terms)}")

        return diagnostics

    def _build_summary(
        self,
        df: pd.DataFrame,
        model: str,
        reasons: list[str],
        plan: list[str],
        bundle: FitBundle,
        diagnostics: list[str],
    ) -> dict[str, Any]:
        result = bundle.result
        term = bundle.main_term
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
            "knowledge_card": METHOD_KNOWLEDGE[model],
            "data_rows_used": int(len(df)),
            "outcome": self.spec.outcome,
            "treatment": self.spec.treatment,
            "controls": self.spec.controls,
            "instrument": self.spec.instrument,
            "entity_id": self.spec.entity_id,
            "time_id": self.spec.time_id,
            "treat_group": self.spec.treat_group,
            "post": self.spec.post,
            "main_term_reported": term,
            "main_result": {
                "coefficient": round(float(result.params[term]), 6),
                "std_error": round(float(se_series[term]), 6),
                "pvalue": round(float(result.pvalues[term]), 6),
                "r_squared": None if r2 is None else round(float(r2), 6),
            },
            "diagnostics": diagnostics,
            "reflection": self.reflection_log,
            "all_terms": terms.to_dict(orient="records"),
        }

    def _emit(self, summary: dict[str, Any]) -> None:
        print("\n=== Lightweight Econometrics Agent v2 ===")
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


def show_knowledge(model: str) -> None:
    if model == "all":
        payload = METHOD_KNOWLEDGE
    else:
        payload = {model: METHOD_KNOWLEDGE[model]}
    print(json.dumps(payload, indent=2, ensure_ascii=False))


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

    did_df = fe_df.copy()
    did_df["treat_group"] = (did_df["firm"] < 20).astype(int)
    did_df["post"] = (did_df["year"] >= 2018).astype(int)
    did_path = output_dir / "did_demo.csv"
    did_df.to_csv(did_path, index=False)

    entities = np.repeat(np.arange(60), 8)
    event_years = np.tile(np.arange(2014, 2022), 60)
    adoption_candidates = {entity: rng.choice([2017, 2018, 2019]) for entity in range(40)}
    treatment_path = []
    dynamic_effect = []
    for entity, year in zip(entities, event_years):
        adoption_year = adoption_candidates.get(entity)
        treated = int(adoption_year is not None and year >= adoption_year)
        treatment_path.append(treated)
        if adoption_year is None or year < adoption_year:
            dynamic_effect.append(0.0)
        else:
            event_time = year - adoption_year
            dynamic_effect.append(min(2.0, 0.8 + 0.4 * event_time))
    entity_fe = rng.normal(size=60).repeat(8)
    year_fe_event_map = {year: shock for year, shock in zip(range(2014, 2022), rng.normal(size=8))}
    year_fe_event = np.array([year_fe_event_map[y] for y in event_years])
    x_event = rng.normal(size=len(entities))
    y_event = np.array(dynamic_effect) + 0.4 * x_event + entity_fe + year_fe_event + rng.normal(scale=0.8, size=len(entities))
    event_df = pd.DataFrame({"unit": entities, "year": event_years, "y": y_event, "treated": treatment_path, "x": x_event})
    event_path = output_dir / "event_study_demo.csv"
    event_df.to_csv(event_path, index=False)

    z = rng.normal(size=n)
    u = rng.normal(size=n)
    treat_iv = 0.9 * z + 0.6 * u + rng.normal(scale=0.5, size=n)
    y_iv = 1.8 * treat_iv + 0.5 * c + 0.8 * u + rng.normal(scale=0.8, size=n)
    iv_df = pd.DataFrame({"y": y_iv, "treat": treat_iv, "z": z, "c": c})
    iv_path = output_dir / "iv_demo.csv"
    iv_df.to_csv(iv_path, index=False)

    return {"ols": ols_path, "fe": fe_path, "did": did_path, "event-study": event_path, "iv": iv_path}


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
            data=str(paths["did"]),
            query="run a difference-in-differences design for the policy shock",
            outcome="y",
            treatment="treat",
            controls=["c"],
            entity_id="firm",
            time_id="year",
            treat_group="treat_group",
            post="post",
            model="auto",
        ),
        AnalysisSpec(
            data=str(paths["event-study"]),
            query="run an event study and inspect pre-trends",
            outcome="y",
            treatment="treated",
            controls=["x"],
            entity_id="unit",
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
    parser = argparse.ArgumentParser(description="Knowledge-driven local econometrics agent.")
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
    run_parser.add_argument("--treat-group")
    run_parser.add_argument("--post")
    run_parser.add_argument("--model", choices=["auto", "ols", "fe", "iv", "did", "event-study"], default="auto")
    run_parser.add_argument("--lead-window", type=int, default=4)
    run_parser.add_argument("--lag-window", type=int, default=3)
    run_parser.add_argument("--save-summary")

    demo_parser = subparsers.add_parser("demo", help="Generate demo data and run OLS/FE/DID/Event-Study/IV examples.")
    demo_parser.add_argument("--output-dir", default="lite_demo_output")

    knowledge_parser = subparsers.add_parser("knowledge", help="Print the absorbed econometric knowledge cards.")
    knowledge_parser.add_argument("--model", choices=["all", "ols", "fe", "iv", "did", "event-study"], default="all")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "demo":
        run_demo(args.output_dir)
        return

    if args.command == "knowledge":
        show_knowledge(args.model)
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
        treat_group=args.treat_group,
        post=args.post,
        model=args.model,
        lead_window=args.lead_window,
        lag_window=args.lag_window,
        save_summary=args.save_summary,
    )
    LiteEconometricsAgent(spec).run()


if __name__ == "__main__":
    main()
