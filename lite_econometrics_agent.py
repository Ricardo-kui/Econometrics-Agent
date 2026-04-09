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
from scipy.stats import norm

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
    weights: str | None = None
    cluster: str | None = None
    entity_id: str | None = None
    time_id: str | None = None
    treat_group: str | None = None
    post: str | None = None
    running_variable: str | None = None
    cutoff: float | None = None
    bandwidth: float | None = None
    kernel: str = "triangle"
    rdd_mode: str = "auto"
    poly_order: int = 1
    estimand: str = "ATE"
    matched_num: int = 1
    cov_type: str = "auto"
    hac_maxlags: int = 1
    export_balance: str | None = None
    export_terms: str | None = None
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


@dataclass
class ScalarResult:
    params: pd.Series
    std_errors: pd.Series
    pvalues: pd.Series
    rsquared: float | None = None
    rsquared_adj: float | None = None


def make_scalar_result(term: str, estimate: float, std_error: float | None = None, extra: dict[str, float] | None = None) -> ScalarResult:
    extra = extra or {}
    se = np.nan if std_error is None else float(std_error)
    if se is None or np.isnan(se) or se <= 0:
        pvalue = np.nan
    else:
        z_score = float(estimate) / se
        pvalue = float(2 * (1 - norm.cdf(abs(z_score))))
    params = {"const": 0.0, term: float(estimate)}
    ses = {"const": np.nan, term: se}
    pvalues = {"const": np.nan, term: pvalue}
    for key, value in extra.items():
        params[key] = float(value)
        ses[key] = np.nan
        pvalues[key] = np.nan
    return ScalarResult(
        params=pd.Series(params),
        std_errors=pd.Series(ses),
        pvalues=pd.Series(pvalues),
        rsquared=None,
        rsquared_adj=None,
    )


class RulePlanner:
    IV_KEYWORDS = {"iv", "2sls", "instrument", "instrumental", "endogeneity", "endogenous"}
    FE_KEYWORDS = {"fe", "fixed effect", "fixed effects", "twfe", "panel", "within", "entity effect", "time effect"}
    DID_KEYWORDS = {"did", "difference in difference", "difference-in-differences", "policy shock", "treated", "staggered adoption"}
    EVENT_STUDY_KEYWORDS = {"event study", "dynamic effect", "dynamic treatment", "lead", "lag", "pretrend", "pre-trend"}
    RDD_KEYWORDS = {"rdd", "regression discontinuity", "discontinuity", "cutoff", "threshold"}
    FUZZY_RDD_KEYWORDS = {"fuzzy rdd", "fuzzy regression discontinuity", "fuzzy discontinuity"}
    GLOBAL_POLY_KEYWORDS = {"global polynomial", "polynomial rdd", "higher-order polynomial", "global poly"}
    PSM_KEYWORDS = {"psm", "matching", "matched", "propensity score"}
    IPW_KEYWORDS = {"ipw", "inverse probability weighting", "propensity weighting"}
    AIPW_KEYWORDS = {"aipw", "augmented ipw", "double robust", "doubly robust", "augmented inverse probability weighting"}
    IPWRA_KEYWORDS = {"ipwra", "ipw regression adjustment", "weighted regression adjustment", "doubly robust regression adjustment"}

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
        rdd_requested = any(keyword in query for keyword in cls.RDD_KEYWORDS)
        fuzzy_rdd_requested = any(keyword in query for keyword in cls.FUZZY_RDD_KEYWORDS)
        global_poly_requested = any(keyword in query for keyword in cls.GLOBAL_POLY_KEYWORDS)
        psm_requested = any(keyword in query for keyword in cls.PSM_KEYWORDS)
        ipw_requested = any(keyword in query for keyword in cls.IPW_KEYWORDS)
        aipw_requested = any(keyword in query for keyword in cls.AIPW_KEYWORDS)
        ipwra_requested = any(keyword in query for keyword in cls.IPWRA_KEYWORDS)

        if spec.model != "auto":
            return spec.model, [f"user_forced_model={spec.model}"]

        if spec.instrument:
            reasons.append("instrument was provided explicitly")
            return "iv", reasons

        if any(keyword in query for keyword in cls.IV_KEYWORDS):
            reasons.append("query mentions IV / endogenous treatment")
            return "iv", reasons

        if spec.running_variable and spec.cutoff is not None and fuzzy_rdd_requested:
            reasons.append("running variable and cutoff were provided explicitly for a fuzzy RDD task")
            if global_poly_requested:
                reasons.append("query asks for a global polynomial discontinuity specification")
            return "fuzzy-rdd", reasons

        if spec.running_variable and spec.cutoff is not None:
            reasons.append("running variable and cutoff were provided explicitly")
            if global_poly_requested:
                reasons.append("query asks for a global polynomial discontinuity specification")
            return "rdd", reasons

        if fuzzy_rdd_requested and spec.running_variable:
            reasons.append("query explicitly requests fuzzy RDD and a running variable is available")
            return "fuzzy-rdd", reasons

        if rdd_requested and spec.running_variable:
            reasons.append("query mentions a discontinuity design and a running variable is available")
            return "rdd", reasons

        if aipw_requested and spec.treatment in df.columns and is_binary_like(df[spec.treatment]):
            reasons.append("query explicitly requests a doubly robust AIPW estimator")
            return "aipw", reasons

        if ipwra_requested and spec.treatment in df.columns and is_binary_like(df[spec.treatment]):
            reasons.append("query explicitly requests IPW regression adjustment")
            return "ipwra", reasons

        if ipw_requested and spec.treatment in df.columns and is_binary_like(df[spec.treatment]):
            reasons.append("query explicitly requests inverse-probability weighting")
            return "ipw", reasons

        if psm_requested and spec.treatment in df.columns and is_binary_like(df[spec.treatment]):
            reasons.append("query explicitly requests propensity-score matching")
            return "psm", reasons

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
    def _user_weights(df: pd.DataFrame, spec: AnalysisSpec) -> pd.Series | None:
        if not spec.weights:
            return None
        return df[spec.weights].astype(float)

    @staticmethod
    def _iv_fit_kwargs(spec: AnalysisSpec, df: pd.DataFrame) -> dict[str, Any]:
        cov_type = spec.cov_type.lower()
        if cov_type == "hac":
            return {"cov_type": "kernel", "kernel": "bartlett", "bandwidth": spec.hac_maxlags}
        if cov_type == "cluster" or (cov_type == "auto" and spec.cluster):
            return {"cov_type": "clustered", "clusters": df[spec.cluster]}
        if cov_type == "cluster":
            raise ValueError("IV clustered covariance requires --cluster.")
        if cov_type == "cluster-both":
            raise ValueError("two-way cluster is not implemented for IV in this lightweight agent.")
        if cov_type == "robust":
            return {"cov_type": "robust"}
        return {"cov_type": "robust"}

    @staticmethod
    def _fit_statsmodels(
        y: pd.Series,
        X: pd.DataFrame,
        spec: AnalysisSpec,
        weights: pd.Series | None = None,
        cluster_groups: pd.Series | None = None,
    ):
        model = sm.WLS(y, X, weights=weights) if weights is not None else sm.OLS(y, X)
        cov_type = spec.cov_type.lower()
        if cov_type == "hac":
            return model.fit(cov_type="HAC", cov_kwds={"maxlags": spec.hac_maxlags})
        if cov_type == "cluster-both":
            raise ValueError("cluster-both is only supported for panel-style models with entity and time dimensions.")
        if cov_type == "cluster" or (cov_type == "auto" and spec.cluster):
            if cluster_groups is None:
                raise ValueError("cluster groups must be provided when cluster covariance is requested")
            return model.fit(cov_type="cluster", cov_kwds={"groups": cluster_groups})
        return model.fit(cov_type="HC1")

    @staticmethod
    def _panel_fit(y: pd.Series, X: pd.DataFrame, spec: AnalysisSpec, *, entity_effects: bool, time_effects: bool):
        weights = X[[spec.weights]] if spec.weights and spec.weights in X.columns else None
        clean_X = X.drop(columns=[col for col in [spec.weights, spec.cluster] if col], errors="ignore")
        model = PanelOLS(
            y,
            clean_X,
            entity_effects=entity_effects,
            time_effects=time_effects,
            drop_absorbed=True,
            weights=weights,
        )
        cov_type = spec.cov_type.lower()
        if cov_type == "hac":
            return model.fit(cov_type="kernel", kernel="bartlett", bandwidth=spec.hac_maxlags)
        if cov_type == "cluster-both":
            return model.fit(cov_type="clustered", cluster_entity=True, cluster_time=True)
        if cov_type == "cluster" or (cov_type == "auto" and spec.cluster):
            if spec.cluster == spec.entity_id:
                return model.fit(cov_type="clustered", cluster_entity=True)
            if spec.cluster == spec.time_id:
                return model.fit(cov_type="clustered", cluster_time=True)
            if spec.cluster is None:
                if entity_effects and time_effects:
                    return model.fit(cov_type="clustered", cluster_entity=True, cluster_time=True)
                if entity_effects:
                    return model.fit(cov_type="clustered", cluster_entity=True)
                if time_effects:
                    return model.fit(cov_type="clustered", cluster_time=True)
                raise ValueError("cluster covariance for panel models requires --cluster or a panel effect structure.")
            cluster_frame = X[[spec.cluster]]
            return model.fit(cov_type="clustered", clusters=cluster_frame)
        if cov_type == "robust":
            return model.fit(cov_type="robust")
        if entity_effects and time_effects:
            return model.fit(cov_type="clustered", cluster_entity=True, cluster_time=True)
        if entity_effects:
            return model.fit(cov_type="clustered", cluster_entity=True)
        return model.fit(cov_type="robust")

    @staticmethod
    def fit_ols(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        X = sm.add_constant(df[[spec.treatment] + spec.controls], has_constant="add")
        y = df[spec.outcome]
        weights = EconometricTools._user_weights(df, spec)
        cluster_groups = df[spec.cluster] if spec.cluster else None
        result = EconometricTools._fit_statsmodels(y, X, spec, weights=weights, cluster_groups=cluster_groups)
        return FitBundle(result=result, main_term=spec.treatment, model_label="ols")

    @staticmethod
    def fit_fe(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        if not spec.entity_id or not spec.time_id:
            raise ValueError("FE requires both --entity-id and --time-id.")
        panel = df.set_index([spec.entity_id, spec.time_id]).sort_index()
        extra_cols = [spec.cluster] if spec.cluster and spec.cluster not in {spec.entity_id, spec.time_id} else []
        extra_cols += [spec.weights] if spec.weights else []
        X = panel[[spec.treatment] + spec.controls + extra_cols]
        y = panel[spec.outcome]
        result = EconometricTools._panel_fit(y, X, spec, entity_effects=True, time_effects=True)
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
        weights = EconometricTools._user_weights(df, spec)
        fit_kwargs = EconometricTools._iv_fit_kwargs(spec, df)
        result = IV2SLS(y, exog, endog, instruments, weights=weights).fit(**fit_kwargs)
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
            keep_cols = [spec.outcome, spec.entity_id, spec.time_id] + spec.controls
            if spec.cluster and spec.cluster not in {spec.entity_id, spec.time_id}:
                keep_cols.append(spec.cluster)
            if spec.weights:
                keep_cols.append(spec.weights)
            panel = df[keep_cols].copy()
            panel["did"] = interaction
            panel = panel.set_index([spec.entity_id, spec.time_id]).sort_index()
            extra_cols = [spec.cluster] if spec.cluster and spec.cluster not in {spec.entity_id, spec.time_id} else []
            extra_cols += [spec.weights] if spec.weights else []
            X = panel[["did"] + spec.controls + extra_cols]
            y = panel[spec.outcome]
            result = EconometricTools._panel_fit(y, X, spec, entity_effects=True, time_effects=True)
        else:
            X = df[[spec.treat_group, spec.post] + spec.controls].copy()
            X["did"] = interaction
            X = sm.add_constant(X[["did", spec.treat_group, spec.post] + spec.controls], has_constant="add")
            y = df[spec.outcome]
            weights = EconometricTools._user_weights(df, spec)
            cluster_groups = df[spec.cluster] if spec.cluster else None
            result = EconometricTools._fit_statsmodels(y, X, spec, weights=weights, cluster_groups=cluster_groups)
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
        extra_cols = [spec.cluster] if spec.cluster and spec.cluster not in {spec.entity_id, spec.time_id} else []
        extra_cols += [spec.weights] if spec.weights else []
        X = panel[[spec.treatment] + spec.controls + extra_cols]
        y = panel[spec.outcome]
        result = EconometricTools._panel_fit(y, X, spec, entity_effects=True, time_effects=True)
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
        if spec.cluster and spec.cluster not in {spec.entity_id, spec.time_id}:
            columns.append(spec.cluster)
        if spec.weights:
            columns.append(spec.weights)
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
        extra_cols = [spec.cluster] if spec.cluster and spec.cluster not in {spec.entity_id, spec.time_id} else []
        extra_cols += [spec.weights] if spec.weights else []
        X = panel[event_terms + spec.controls + extra_cols]
        y = panel[spec.outcome]
        result = EconometricTools._panel_fit(y, X, spec, entity_effects=True, time_effects=True)
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

    @staticmethod
    def fit_psm(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        propensity, _ = EconometricTools._estimate_propensity(df, spec)
        estimate = EconometricTools._matching_estimate(df, propensity, spec)
        term_name = f"{spec.estimand.lower()}_psm"
        std_error = EconometricTools._bootstrap_scalar_estimate(
            df,
            spec,
            lambda boot_df: EconometricTools._matching_estimate(
                boot_df,
                EconometricTools._estimate_propensity(boot_df, spec)[0],
                spec,
            ),
        )
        result = make_scalar_result(term=term_name, estimate=estimate, std_error=std_error)
        overlap = EconometricTools._overlap_summary(df[spec.treatment], propensity)
        balance = EconometricTools._balance_diagnostics(df, spec, after_weights=EconometricTools._matching_weights(df, propensity, spec))
        return FitBundle(
            result=result,
            main_term=term_name,
            model_label="psm",
            extras={"propensity_range": overlap, "balance_summary": balance, "priority_terms": [term_name]},
        )

    @staticmethod
    def fit_ipw(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        propensity, _ = EconometricTools._estimate_propensity(df, spec)
        estimate, weight_summary = EconometricTools._ipw_estimate(df, propensity, spec)
        term_name = f"{spec.estimand.lower()}_ipw"
        std_error = EconometricTools._bootstrap_scalar_estimate(
            df,
            spec,
            lambda boot_df: EconometricTools._ipw_estimate(
                boot_df,
                EconometricTools._estimate_propensity(boot_df, spec)[0],
                spec,
            )[0],
        )
        result = make_scalar_result(term=term_name, estimate=estimate, std_error=std_error)
        overlap = EconometricTools._overlap_summary(df[spec.treatment], propensity)
        balance = EconometricTools._balance_diagnostics(df, spec, after_weights=weight_summary["weights"])
        return FitBundle(
            result=result,
            main_term=term_name,
            model_label="ipw",
            extras={"propensity_range": overlap, "weight_summary": weight_summary, "balance_summary": balance, "priority_terms": [term_name]},
        )

    @staticmethod
    def fit_aipw(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        propensity, _ = EconometricTools._estimate_propensity(df, spec)
        estimate, details = EconometricTools._aipw_estimate(df, propensity, spec)
        std_error = EconometricTools._bootstrap_scalar_estimate(
            df,
            spec,
            lambda boot_df: EconometricTools._aipw_estimate(
                boot_df,
                EconometricTools._estimate_propensity(boot_df, spec)[0],
                spec,
            )[0],
        )
        term_name = f"{spec.estimand.lower()}_aipw"
        result = make_scalar_result(term=term_name, estimate=estimate, std_error=std_error)
        overlap = EconometricTools._overlap_summary(df[spec.treatment], propensity)
        balance = EconometricTools._balance_diagnostics(df, spec, after_weights=details["ipw_weights"])
        return FitBundle(
            result=result,
            main_term=term_name,
            model_label="aipw",
            extras={
                "propensity_range": overlap,
                "weight_summary": details["weight_summary"],
                "balance_summary": balance,
                "priority_terms": [term_name],
            },
        )

    @staticmethod
    def fit_ipwra(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        propensity, _ = EconometricTools._estimate_propensity(df, spec)
        estimate, details = EconometricTools._ipwra_estimate(df, propensity, spec)
        std_error = EconometricTools._bootstrap_scalar_estimate(
            df,
            spec,
            lambda boot_df: EconometricTools._ipwra_estimate(
                boot_df,
                EconometricTools._estimate_propensity(boot_df, spec)[0],
                spec,
            )[0],
        )
        term_name = f"{spec.estimand.lower()}_ipwra"
        result = make_scalar_result(term=term_name, estimate=estimate, std_error=std_error)
        overlap = EconometricTools._overlap_summary(df[spec.treatment], propensity)
        balance = EconometricTools._balance_diagnostics(df, spec, after_weights=details["ipw_weights"])
        return FitBundle(
            result=result,
            main_term=term_name,
            model_label="ipwra",
            extras={
                "propensity_range": overlap,
                "weight_summary": details["weight_summary"],
                "balance_summary": balance,
                "priority_terms": [term_name],
            },
        )

    @staticmethod
    def fit_rdd(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        if spec.running_variable is None or spec.cutoff is None:
            raise ValueError("RDD requires --running-variable and --cutoff.")
        if not is_binary_like(df[spec.treatment]):
            raise ValueError("Sharp RDD requires a binary treatment indicator.")

        selected, bandwidth = EconometricTools._select_rdd_sample(df, spec)
        centered = (selected[spec.running_variable] - spec.cutoff).rename("running_centered")
        X, base_weights = EconometricTools._build_sharp_rdd_design(selected, centered, bandwidth, spec)
        X = sm.add_constant(X, has_constant="add")
        y = selected[spec.outcome]
        cluster_groups = selected[spec.cluster] if spec.cluster else None
        result = EconometricTools._fit_statsmodels(y, X, spec, weights=base_weights, cluster_groups=cluster_groups)
        return FitBundle(
            result=result,
            main_term=spec.treatment,
            model_label="rdd",
            extras={
                "bandwidth_used": bandwidth,
                "n_left": int((selected[spec.running_variable] < spec.cutoff).sum()),
                "n_right": int((selected[spec.running_variable] >= spec.cutoff).sum()),
                "rdd_mode": spec.rdd_mode,
                "poly_order": spec.poly_order,
                "priority_terms": [spec.treatment],
            },
        )

    @staticmethod
    def fit_fuzzy_rdd(df: pd.DataFrame, spec: AnalysisSpec) -> FitBundle:
        if spec.running_variable is None or spec.cutoff is None:
            raise ValueError("Fuzzy RDD requires --running-variable and --cutoff.")

        selected, bandwidth = EconometricTools._select_rdd_sample(df, spec)
        running = selected[spec.running_variable].astype(float)
        assignment = (running >= spec.cutoff).astype(float).rename("cutoff_assignment")
        centered = (running - spec.cutoff).rename("running_centered")
        y = selected[spec.outcome]
        exog, weights = EconometricTools._build_fuzzy_rdd_design(selected, centered, assignment, bandwidth, spec)
        endog = selected[spec.treatment].astype(float)
        instruments = pd.DataFrame({"cutoff_assignment": assignment}, index=selected.index)
        fit_kwargs = EconometricTools._iv_fit_kwargs(spec, selected)
        result = IV2SLS(y, exog, endog, instruments, weights=weights).fit(**fit_kwargs)
        return FitBundle(
            result=result,
            main_term=spec.treatment,
            model_label="fuzzy-rdd",
            extras={
                "bandwidth_used": bandwidth,
                "n_left": int((selected[spec.running_variable] < spec.cutoff).sum()),
                "n_right": int((selected[spec.running_variable] >= spec.cutoff).sum()),
                "rdd_mode": spec.rdd_mode,
                "poly_order": spec.poly_order,
                "priority_terms": [spec.treatment],
            },
        )

    @staticmethod
    def _estimate_propensity(df: pd.DataFrame, spec: AnalysisSpec) -> tuple[pd.Series, Any]:
        if not spec.controls:
            raise ValueError("PSM/IPW require at least one control variable to estimate a propensity score.")
        if not is_binary_like(df[spec.treatment]):
            raise ValueError("PSM/IPW require a binary treatment variable.")
        X = sm.add_constant(df[spec.controls], has_constant="add")
        model = sm.Logit(df[spec.treatment].astype(float), X).fit(disp=False)
        propensity = pd.Series(model.predict(X), index=df.index, name="propensity_score").clip(1e-3, 1 - 1e-3)
        return propensity, model

    @staticmethod
    def _matching_estimate(df: pd.DataFrame, propensity: pd.Series, spec: AnalysisSpec) -> float:
        treated = df[spec.treatment].astype(int)
        outcome = df[spec.outcome].astype(float)
        k = max(int(spec.matched_num), 1)
        treat_index = treated[treated == 1].index
        control_index = treated[treated == 0].index
        if len(treat_index) == 0 or len(control_index) == 0:
            raise ValueError("PSM requires both treated and control observations.")

        def matched_mean(source_idx, target_idx):
            estimates = []
            for idx in source_idx:
                distances = (propensity.loc[target_idx] - propensity.loc[idx]).abs().sort_values()
                neighbors = distances.head(k).index
                estimates.append(float(outcome.loc[neighbors].mean()))
            return pd.Series(estimates, index=source_idx)

        if spec.estimand.upper() == "ATT":
            matched_control = matched_mean(treat_index, control_index)
            return float(outcome.loc[treat_index].mean() - matched_control.mean())

        matched_control = matched_mean(treat_index, control_index)
        matched_treated = matched_mean(control_index, treat_index)
        return float(
            pd.concat([outcome.loc[treat_index], matched_treated]).mean()
            - pd.concat([outcome.loc[control_index], matched_control]).mean()
        )

    @staticmethod
    def _matching_weights(df: pd.DataFrame, propensity: pd.Series, spec: AnalysisSpec) -> pd.Series:
        treated = df[spec.treatment].astype(int)
        k = max(int(spec.matched_num), 1)
        weights = pd.Series(0.0, index=df.index)
        treat_index = treated[treated == 1].index
        control_index = treated[treated == 0].index
        if spec.estimand.upper() == "ATT":
            weights.loc[treat_index] = 1.0
            for idx in treat_index:
                neighbors = (propensity.loc[control_index] - propensity.loc[idx]).abs().sort_values().head(k).index
                weights.loc[neighbors] += 1.0 / k
            return weights

        weights.loc[:] = 1.0
        for idx in treat_index:
            neighbors = (propensity.loc[control_index] - propensity.loc[idx]).abs().sort_values().head(k).index
            weights.loc[neighbors] += 1.0 / k
        for idx in control_index:
            neighbors = (propensity.loc[treat_index] - propensity.loc[idx]).abs().sort_values().head(k).index
            weights.loc[neighbors] += 1.0 / k
        return weights

    @staticmethod
    def _ipw_estimate(df: pd.DataFrame, propensity: pd.Series, spec: AnalysisSpec) -> tuple[float, dict[str, float]]:
        treated = df[spec.treatment].astype(float)
        outcome = df[spec.outcome].astype(float)
        sample_weights = df[spec.weights].astype(float) if spec.weights else pd.Series(1.0, index=df.index)

        if spec.estimand.upper() == "ATT":
            w_t = sample_weights * treated
            w_c = sample_weights * (1 - treated) * propensity / (1 - propensity)
            estimate = float((w_t * outcome).sum() / w_t.sum() - (w_c * outcome).sum() / w_c.sum())
            final_weights = w_t + w_c
        else:
            w_t = sample_weights * treated / propensity
            w_c = sample_weights * (1 - treated) / (1 - propensity)
            estimate = float((w_t * outcome).sum() / w_t.sum() - (w_c * outcome).sum() / w_c.sum())
            final_weights = w_t + w_c

        summary = {
            "weight_min": float(final_weights.min()),
            "weight_p95": float(final_weights.quantile(0.95)),
            "weight_max": float(final_weights.max()),
            "weights": final_weights,
        }
        return estimate, summary

    @staticmethod
    def _aipw_estimate(df: pd.DataFrame, propensity: pd.Series, spec: AnalysisSpec) -> tuple[float, dict[str, Any]]:
        treated = df[spec.treatment].astype(float)
        outcome = df[spec.outcome].astype(float)
        sample_weights = df[spec.weights].astype(float) if spec.weights else pd.Series(1.0, index=df.index)

        X = sm.add_constant(df[spec.controls], has_constant="add")
        treated_mask = treated == 1
        control_mask = treated == 0
        if treated_mask.sum() == 0 or control_mask.sum() == 0:
            raise ValueError("AIPW requires both treated and control observations.")

        model_treated = sm.WLS(outcome.loc[treated_mask], X.loc[treated_mask], weights=sample_weights.loc[treated_mask]).fit()
        model_control = sm.WLS(outcome.loc[control_mask], X.loc[control_mask], weights=sample_weights.loc[control_mask]).fit()
        mu1 = pd.Series(model_treated.predict(X), index=df.index)
        mu0 = pd.Series(model_control.predict(X), index=df.index)

        estimand = spec.estimand.upper()
        if estimand == "ATT":
            p_treated = float((sample_weights * treated).sum() / sample_weights.sum())
            if p_treated <= 0:
                raise ValueError("ATT AIPW requires treated observations.")
            term_treated = sample_weights * treated * (outcome - mu0)
            term_control = sample_weights * (1 - treated) * propensity / (1 - propensity) * (outcome - mu0)
            estimate = float((term_treated.sum() - term_control.sum()) / (sample_weights * treated).sum())
            ipw_weights = sample_weights * (treated + (1 - treated) * propensity / (1 - propensity))
        else:
            influence = mu1 - mu0 + treated * (outcome - mu1) / propensity - (1 - treated) * (outcome - mu0) / (1 - propensity)
            estimate = float((sample_weights * influence).sum() / sample_weights.sum())
            ipw_weights = sample_weights * (treated / propensity + (1 - treated) / (1 - propensity))
        details = {
            "ipw_weights": ipw_weights,
            "weight_summary": {
                "weight_min": float(ipw_weights.min()),
                "weight_p95": float(ipw_weights.quantile(0.95)),
                "weight_max": float(ipw_weights.max()),
            },
        }
        return estimate, details

    @staticmethod
    def _ipwra_estimate(df: pd.DataFrame, propensity: pd.Series, spec: AnalysisSpec) -> tuple[float, dict[str, Any]]:
        treated = df[spec.treatment].astype(float)
        outcome = df[spec.outcome].astype(float)
        sample_weights = df[spec.weights].astype(float) if spec.weights else pd.Series(1.0, index=df.index)
        X = sm.add_constant(df[spec.controls], has_constant="add")
        treated_mask = treated == 1
        control_mask = treated == 0
        if treated_mask.sum() == 0 or control_mask.sum() == 0:
            raise ValueError("IPWRA requires both treated and control observations.")

        estimand = spec.estimand.upper()
        if estimand == "ATT":
            w_t = sample_weights.loc[treated_mask]
            w_c = (sample_weights * propensity / (1 - propensity)).loc[control_mask]
            model_treated = sm.WLS(outcome.loc[treated_mask], X.loc[treated_mask], weights=w_t).fit()
            model_control = sm.WLS(outcome.loc[control_mask], X.loc[control_mask], weights=w_c).fit()
            mu1 = pd.Series(model_treated.predict(X), index=df.index)
            mu0 = pd.Series(model_control.predict(X), index=df.index)
            estimate = float(np.average((mu1 - mu0).loc[treated_mask], weights=sample_weights.loc[treated_mask]))
            ipw_weights = sample_weights * (treated + (1 - treated) * propensity / (1 - propensity))
        else:
            w_t = (sample_weights / propensity).loc[treated_mask]
            w_c = (sample_weights / (1 - propensity)).loc[control_mask]
            model_treated = sm.WLS(outcome.loc[treated_mask], X.loc[treated_mask], weights=w_t).fit()
            model_control = sm.WLS(outcome.loc[control_mask], X.loc[control_mask], weights=w_c).fit()
            mu1 = pd.Series(model_treated.predict(X), index=df.index)
            mu0 = pd.Series(model_control.predict(X), index=df.index)
            estimate = float(np.average(mu1 - mu0, weights=sample_weights))
            ipw_weights = sample_weights * (treated / propensity + (1 - treated) / (1 - propensity))
        details = {
            "ipw_weights": ipw_weights,
            "weight_summary": {
                "weight_min": float(ipw_weights.min()),
                "weight_p95": float(ipw_weights.quantile(0.95)),
                "weight_max": float(ipw_weights.max()),
            },
        }
        return estimate, details

    @staticmethod
    def _select_rdd_sample(df: pd.DataFrame, spec: AnalysisSpec) -> tuple[pd.DataFrame, float]:
        selected = df.copy()
        running = selected[spec.running_variable].astype(float)
        use_full_sample = spec.rdd_mode == "global-poly" and spec.bandwidth is None
        bandwidth = max(running.max() - spec.cutoff, spec.cutoff - running.min()) if spec.bandwidth is None else float(spec.bandwidth)
        if bandwidth <= 0:
            raise ValueError("RDD bandwidth must be positive.")
        if not use_full_sample:
            selected = selected[(running >= spec.cutoff - bandwidth) & (running <= spec.cutoff + bandwidth)].copy()
        if selected.empty:
            raise ValueError("RDD bandwidth leaves no usable observations.")
        if (selected[spec.running_variable] >= spec.cutoff).sum() == 0 or (selected[spec.running_variable] < spec.cutoff).sum() == 0:
            raise ValueError("RDD requires support on both sides of the cutoff.")
        return selected, bandwidth

    @staticmethod
    def _build_sharp_rdd_design(selected: pd.DataFrame, centered: pd.Series, bandwidth: float, spec: AnalysisSpec) -> tuple[pd.DataFrame, pd.Series | None]:
        base = pd.DataFrame(index=selected.index)
        base[spec.treatment] = selected[spec.treatment]
        if spec.rdd_mode == "global-poly":
            for order in range(1, max(spec.poly_order, 1) + 1):
                term = centered.pow(order).rename(f"running_order_{order}")
                interaction = (term * selected[spec.treatment]).rename(f"running_order_{order}_interaction")
                base[term.name] = term
                base[interaction.name] = interaction
            weights = selected[spec.weights].astype(float) if spec.weights else None
        else:
            interaction = (centered * selected[spec.treatment]).rename("running_interaction")
            base[centered.name] = centered
            base[interaction.name] = interaction
            weights = EconometricTools._kernel_weights(centered, bandwidth, spec.kernel)
            if spec.weights:
                weights = weights * selected[spec.weights].astype(float)
        for col in spec.controls:
            base[col] = selected[col]
        return base, weights

    @staticmethod
    def _build_fuzzy_rdd_design(
        selected: pd.DataFrame,
        centered: pd.Series,
        assignment: pd.Series,
        bandwidth: float,
        spec: AnalysisSpec,
    ) -> tuple[pd.DataFrame, pd.Series | None]:
        exog = pd.DataFrame({"const": 1.0}, index=selected.index)
        if spec.rdd_mode == "global-poly":
            for order in range(1, max(spec.poly_order, 1) + 1):
                term = centered.pow(order).rename(f"running_order_{order}")
                interaction = (term * assignment).rename(f"running_order_{order}_interaction")
                exog[term.name] = term
                exog[interaction.name] = interaction
            weights = selected[spec.weights].astype(float) if spec.weights else None
        else:
            interaction = (centered * assignment).rename("running_interaction")
            exog[centered.name] = centered
            exog[interaction.name] = interaction
            weights = EconometricTools._kernel_weights(centered, bandwidth, spec.kernel)
            if spec.weights:
                weights = weights * selected[spec.weights].astype(float)
        for col in spec.controls:
            exog[col] = selected[col]
        return exog, weights

    @staticmethod
    def _balance_diagnostics(df: pd.DataFrame, spec: AnalysisSpec, after_weights: pd.Series | None = None) -> dict[str, Any]:
        treated = df[spec.treatment].astype(int)
        before: dict[str, float] = {}
        after: dict[str, float] = {}
        rows: list[dict[str, float | str]] = []
        control_mask = treated == 0
        treat_mask = treated == 1
        if not spec.controls:
            return {"before": before, "after": after, "max_abs_before": np.nan, "max_abs_after": np.nan, "table": pd.DataFrame()}

        for col in spec.controls:
            x = df[col].astype(float)
            mean_t = x.loc[treat_mask].mean()
            mean_c = x.loc[control_mask].mean()
            pooled_sd = np.sqrt((x.loc[treat_mask].var(ddof=1) + x.loc[control_mask].var(ddof=1)) / 2)
            before[col] = 0.0 if pooled_sd == 0 or np.isnan(pooled_sd) else float((mean_t - mean_c) / pooled_sd)
            row = {
                "covariate": col,
                "treated_mean_before": float(mean_t),
                "control_mean_before": float(mean_c),
                "smd_before": float(before[col]),
            }
            if after_weights is not None:
                wt = after_weights.loc[treat_mask]
                wc = after_weights.loc[control_mask]
                mean_t_w = np.average(x.loc[treat_mask], weights=wt)
                mean_c_w = np.average(x.loc[control_mask], weights=wc)
                var_t_w = np.average((x.loc[treat_mask] - mean_t_w) ** 2, weights=wt)
                var_c_w = np.average((x.loc[control_mask] - mean_c_w) ** 2, weights=wc)
                pooled_sd_w = np.sqrt((var_t_w + var_c_w) / 2)
                after[col] = 0.0 if pooled_sd_w == 0 or np.isnan(pooled_sd_w) else float((mean_t_w - mean_c_w) / pooled_sd_w)
                row.update(
                    {
                        "treated_mean_after": float(mean_t_w),
                        "control_mean_after": float(mean_c_w),
                        "smd_after": float(after[col]),
                    }
                )
            else:
                row.update(
                    {
                        "treated_mean_after": np.nan,
                        "control_mean_after": np.nan,
                        "smd_after": np.nan,
                    }
                )
            rows.append(row)

        max_before = max(abs(v) for v in before.values()) if before else np.nan
        max_after = max(abs(v) for v in after.values()) if after else np.nan
        return {
            "before": before,
            "after": after,
            "max_abs_before": max_before,
            "max_abs_after": max_after,
            "table": pd.DataFrame(rows),
        }

    @staticmethod
    def _bootstrap_scalar_estimate(df: pd.DataFrame, spec: AnalysisSpec, estimator, reps: int = 30) -> float:
        estimates = []
        n = len(df)
        if n < 20:
            return np.nan
        for seed in range(reps):
            boot_df = df.sample(n=n, replace=True, random_state=seed).reset_index(drop=True)
            try:
                estimates.append(float(estimator(boot_df)))
            except Exception:
                continue
        if len(estimates) < 5:
            return np.nan
        return float(np.std(estimates, ddof=1))

    @staticmethod
    def _overlap_summary(treatment: pd.Series, propensity: pd.Series) -> dict[str, float]:
        treated_scores = propensity.loc[treatment.astype(int) == 1]
        control_scores = propensity.loc[treatment.astype(int) == 0]
        return {
            "treated_min": float(treated_scores.min()),
            "treated_max": float(treated_scores.max()),
            "control_min": float(control_scores.min()),
            "control_max": float(control_scores.max()),
        }

    @staticmethod
    def _kernel_weights(centered_running: pd.Series, bandwidth: float, kernel: str) -> pd.Series:
        scaled = (centered_running / bandwidth).abs()
        if kernel == "uniform":
            weights = pd.Series(1.0, index=centered_running.index)
        elif kernel == "triangle":
            weights = 1 - scaled
        else:
            weights = 0.75 * (1 - scaled.pow(2))
        return weights.clip(lower=1e-8)


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
        self._maybe_export_balance(bundle)
        self._maybe_export_terms(summary)
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
        rdd_like = any(keyword in query for keyword in RulePlanner.RDD_KEYWORDS)
        fuzzy_rdd_like = any(keyword in query for keyword in RulePlanner.FUZZY_RDD_KEYWORDS)
        global_poly_like = any(keyword in query for keyword in RulePlanner.GLOBAL_POLY_KEYWORDS)
        if did_like and model not in {"did", "event-study"}:
            self.reflection_log.append(
                "query mentions a DID-style design, but available variables were insufficient for DID routing; review treat_group/post or panel treatment timing"
            )
        if rdd_like and model not in {"rdd", "fuzzy-rdd"}:
            self.reflection_log.append(
                "query mentions a discontinuity design, but running-variable / cutoff inputs were insufficient for RDD routing"
            )
        if fuzzy_rdd_like and model != "fuzzy-rdd":
            self.reflection_log.append(
                "query mentions fuzzy RDD, but the available discontinuity inputs did not trigger fuzzy-RDD routing"
            )
        if global_poly_like and model in {"rdd", "fuzzy-rdd"} and self.spec.rdd_mode == "auto":
            self.spec.rdd_mode = "global-poly"
            if self.spec.poly_order < 2:
                self.spec.poly_order = 2
            self.reflection_log.append("query suggests a global-polynomial RDD specification, so rdd_mode was upgraded to global-poly")
        if model == "event-study" and RulePlanner.has_panel_structure(self.spec, df):
            self.reflection_log.append("event-study was selected because the task asks for dynamic treatment effects in panel data")

    def _maybe_export_balance(self, bundle: FitBundle) -> None:
        if not self.spec.export_balance:
            return
        balance = bundle.extras.get("balance_summary", {})
        table = balance.get("table")
        if table is None:
            self.reflection_log.append("requested balance-table export, but this method does not produce a balance table")
            return
        output_path = Path(self.spec.export_balance)
        table.to_csv(output_path, index=False)
        self.reflection_log.append(f"exported balance table to {output_path}")

    def _maybe_export_terms(self, summary: dict[str, Any]) -> None:
        if not self.spec.export_terms:
            return
        table = pd.DataFrame(summary["all_terms"])
        output_path = Path(self.spec.export_terms)
        suffix = output_path.suffix.lower()
        if suffix in {".tex", ".latex"}:
            output_path.write_text(self._dataframe_to_latex(table), encoding="utf-8")
        else:
            table.to_csv(output_path, index=False)
        self.reflection_log.append(f"exported coefficient table to {output_path}")

    @staticmethod
    def _dataframe_to_latex(table: pd.DataFrame) -> str:
        def fmt(value: Any) -> str:
            if pd.isna(value):
                return ""
            if isinstance(value, (int, float, np.floating)):
                return f"{float(value):.6f}"
            text = str(value)
            return text.replace("_", "\\_")

        cols = list(table.columns)
        header = " & ".join(cols) + r" \\"
        rows = [" & ".join(fmt(row[col]) for col in cols) + r" \\" for _, row in table.iterrows()]
        alignment = "l" + "r" * (len(cols) - 1)
        body = "\n".join(rows)
        return "\n".join(
            [
                rf"\begin{{tabular}}{{{alignment}}}",
                r"\hline",
                header,
                r"\hline",
                body,
                r"\hline",
                r"\end{tabular}",
            ]
        )

    def _prepare_data(self, df: pd.DataFrame, model: str) -> pd.DataFrame:
        frame = df.copy()

        resolved = {}
        for field_name in ["outcome", "treatment", "instrument", "weights", "cluster", "entity_id", "time_id", "treat_group", "post", "running_variable"]:
            value = getattr(self.spec, field_name)
            if value:
                resolved[field_name] = self._resolve_column(frame, value)
        resolved_controls = [self._resolve_column(frame, col) for col in self.spec.controls]

        self.spec.outcome = resolved["outcome"]
        self.spec.treatment = resolved["treatment"]
        self.spec.controls = resolved_controls
        self.spec.instrument = resolved.get("instrument")
        self.spec.weights = resolved.get("weights")
        self.spec.cluster = resolved.get("cluster")
        self.spec.entity_id = resolved.get("entity_id")
        self.spec.time_id = resolved.get("time_id")
        self.spec.treat_group = resolved.get("treat_group")
        self.spec.post = resolved.get("post")
        self.spec.running_variable = resolved.get("running_variable")

        numeric_cols = [self.spec.outcome, self.spec.treatment] + self.spec.controls
        for optional_col in [self.spec.instrument, self.spec.treat_group, self.spec.post, self.spec.weights, self.spec.running_variable]:
            if optional_col:
                numeric_cols.append(optional_col)
        for col in numeric_cols:
            before_na = frame[col].isna().sum()
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
            after_na = frame[col].isna().sum()
            if after_na > before_na:
                self.reflection_log.append(f"coerced {col} to numeric and introduced {after_na - before_na} NaNs")

        required = [self.spec.outcome, self.spec.treatment] + self.spec.controls
        for optional_col in [self.spec.instrument, self.spec.treat_group, self.spec.post, self.spec.weights, self.spec.running_variable, self.spec.entity_id, self.spec.time_id]:
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

        if self.spec.weights and (frame[self.spec.weights] <= 0).any():
            raise ValueError("Weights must be strictly positive.")

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

        if model in {"psm", "ipw", "aipw"} and not self.spec.controls:
            raise ValueError("PSM/IPW/AIPW require at least one control variable for the propensity model.")

        if model in {"rdd", "fuzzy-rdd"}:
            if self.spec.running_variable is None or self.spec.cutoff is None:
                raise ValueError("RDD requires --running-variable and --cutoff.")
            self.spec.kernel = self.spec.kernel.lower()
            self.spec.rdd_mode = self.spec.rdd_mode.lower()
            if self.spec.kernel not in {"uniform", "triangle", "epanechnikov"}:
                raise ValueError("RDD kernel must be one of: uniform, triangle, epanechnikov.")
            if self.spec.rdd_mode not in {"auto", "local-linear", "global-poly"}:
                raise ValueError("rdd-mode must be one of: auto, local-linear, global-poly.")
            if self.spec.rdd_mode == "auto":
                self.spec.rdd_mode = "local-linear"
            if self.spec.poly_order < 1:
                raise ValueError("poly-order must be at least 1.")
            if self.spec.rdd_mode == "global-poly" and self.spec.poly_order < 2:
                self.spec.poly_order = 2

        return frame

    def _fit_model(self, df: pd.DataFrame, model: str) -> FitBundle:
        tools = {
            "ols": EconometricTools.fit_ols,
            "fe": EconometricTools.fit_fe,
            "iv": EconometricTools.fit_iv,
            "did": EconometricTools.fit_did,
            "event-study": EconometricTools.fit_event_study,
            "rdd": EconometricTools.fit_rdd,
            "fuzzy-rdd": EconometricTools.fit_fuzzy_rdd,
            "psm": EconometricTools.fit_psm,
            "ipw": EconometricTools.fit_ipw,
            "aipw": EconometricTools.fit_aipw,
            "ipwra": EconometricTools.fit_ipwra,
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

        if model in {"psm", "ipw", "aipw", "ipwra"}:
            overlap = bundle.extras.get("propensity_range", {})
            if overlap:
                diagnostics.append(
                    "propensity support: treated [{treated_min:.3f}, {treated_max:.3f}], control [{control_min:.3f}, {control_max:.3f}]".format(**overlap)
                )
            balance = bundle.extras.get("balance_summary", {})
            if balance:
                diagnostics.append(
                    f"max abs standardized mean difference: before={balance.get('max_abs_before'):.3f}, after={balance.get('max_abs_after'):.3f}"
                )
            if model in {"ipw", "aipw", "ipwra"}:
                weight_summary = bundle.extras.get("weight_summary", {})
                if weight_summary:
                    diagnostics.append(
                        "IPW weight summary: min={weight_min:.3f}, p95={weight_p95:.3f}, max={weight_max:.3f}".format(**weight_summary)
                    )
            if model == "aipw":
                diagnostics.append("AIPW is doubly robust, not assumption-free; it still relies on overlap and one of the nuisance models being correctly specified")
            if model == "ipwra":
                diagnostics.append("IPWRA is doubly robust, but it still depends on overlap and sensible regression specification")

        if model == "rdd":
            diagnostics.append(
                f"RDD support near cutoff: left={bundle.extras.get('n_left', 0)}, right={bundle.extras.get('n_right', 0)}, bandwidth={bundle.extras.get('bandwidth_used')}"
            )
            if bundle.extras.get("rdd_mode") == "global-poly":
                diagnostics.append(f"RDD global polynomial order: {bundle.extras.get('poly_order')}")

        if model == "fuzzy-rdd":
            diagnostics.append(
                f"Fuzzy RDD support near cutoff: left={bundle.extras.get('n_left', 0)}, right={bundle.extras.get('n_right', 0)}, bandwidth={bundle.extras.get('bandwidth_used')}"
            )
            if bundle.extras.get("rdd_mode") == "global-poly":
                diagnostics.append(f"Fuzzy RDD global polynomial order: {bundle.extras.get('poly_order')}")
            if hasattr(result, "first_stage"):
                try:
                    first_stage = result.first_stage.diagnostics
                    if not first_stage.empty:
                        first_row = first_stage.iloc[0].to_dict()
                        f_stat = first_row.get("f.stat") or first_row.get("f")
                        if f_stat is not None:
                            diagnostics.append(f"cutoff first-stage F statistic: {float(f_stat):.2f}")
                except Exception as exc:
                    diagnostics.append(f"could not parse fuzzy-RDD first-stage diagnostics: {exc}")

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
            "weights": self.spec.weights,
            "cluster": self.spec.cluster,
            "cov_type": self.spec.cov_type,
            "hac_maxlags": self.spec.hac_maxlags,
            "entity_id": self.spec.entity_id,
            "time_id": self.spec.time_id,
            "treat_group": self.spec.treat_group,
            "post": self.spec.post,
            "running_variable": self.spec.running_variable,
            "cutoff": self.spec.cutoff,
            "bandwidth": self.spec.bandwidth,
            "kernel": self.spec.kernel,
            "rdd_mode": self.spec.rdd_mode,
            "poly_order": self.spec.poly_order,
            "estimand": self.spec.estimand,
            "export_balance": self.spec.export_balance,
            "export_terms": self.spec.export_terms,
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

    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    latent = 0.9 * x1 - 0.6 * x2 + rng.normal(scale=0.8, size=n)
    prob = 1 / (1 + np.exp(-latent))
    treat_ps = (rng.random(size=n) < prob).astype(int)
    y_ps = 1.6 * treat_ps + 0.7 * x1 - 0.4 * x2 + rng.normal(scale=1.0, size=n)
    ps_df = pd.DataFrame({"y": y_ps, "treat": treat_ps, "x1": x1, "x2": x2})
    psm_path = output_dir / "psm_demo.csv"
    ps_df.to_csv(psm_path, index=False)

    running = rng.uniform(-2, 2, size=n)
    treat_rdd = (running >= 0).astype(int)
    fuzzy_prob = np.where(running >= 0, 0.8, 0.2)
    treat_fuzzy = (rng.random(size=n) < fuzzy_prob).astype(int)
    y_rdd = 2.2 * treat_rdd + 0.8 * running - 0.3 * running * treat_rdd + rng.normal(scale=0.7, size=n)
    y_fuzzy = 1.9 * treat_fuzzy + 0.7 * running - 0.2 * running * (running >= 0).astype(int) + rng.normal(scale=0.8, size=n)
    rdd_df = pd.DataFrame({"y": y_rdd, "treat": treat_rdd, "y_fuzzy": y_fuzzy, "treat_fuzzy": treat_fuzzy, "score": running, "x": rng.normal(size=n)})
    rdd_path = output_dir / "rdd_demo.csv"
    rdd_df.to_csv(rdd_path, index=False)

    z = rng.normal(size=n)
    u = rng.normal(size=n)
    treat_iv = 0.9 * z + 0.6 * u + rng.normal(scale=0.5, size=n)
    y_iv = 1.8 * treat_iv + 0.5 * c + 0.8 * u + rng.normal(scale=0.8, size=n)
    iv_df = pd.DataFrame({"y": y_iv, "treat": treat_iv, "z": z, "c": c})
    iv_path = output_dir / "iv_demo.csv"
    iv_df.to_csv(iv_path, index=False)

    return {
        "ols": ols_path,
        "fe": fe_path,
        "did": did_path,
        "event-study": event_path,
        "psm": psm_path,
        "rdd": rdd_path,
        "iv": iv_path,
    }


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
            data=str(paths["psm"]),
            query="estimate the treatment effect with propensity score matching",
            outcome="y",
            treatment="treat",
            controls=["x1", "x2"],
            model="auto",
        ),
        AnalysisSpec(
            data=str(paths["psm"]),
            query="estimate the treatment effect with inverse probability weighting",
            outcome="y",
            treatment="treat",
            controls=["x1", "x2"],
            model="auto",
        ),
        AnalysisSpec(
            data=str(paths["psm"]),
            query="estimate the treatment effect with doubly robust augmented IPW",
            outcome="y",
            treatment="treat",
            controls=["x1", "x2"],
            estimand="ATT",
            model="auto",
        ),
        AnalysisSpec(
            data=str(paths["psm"]),
            query="estimate the treatment effect with IPW regression adjustment",
            outcome="y",
            treatment="treat",
            controls=["x1", "x2"],
            estimand="ATT",
            model="ipwra",
        ),
        AnalysisSpec(
            data=str(paths["rdd"]),
            query="run a sharp RDD around the score cutoff",
            outcome="y",
            treatment="treat",
            controls=["x"],
            running_variable="score",
            cutoff=0.0,
            rdd_mode="global-poly",
            poly_order=3,
            model="auto",
        ),
        AnalysisSpec(
            data=str(paths["rdd"]),
            query="run a fuzzy RDD around the score cutoff",
            outcome="y_fuzzy",
            treatment="treat_fuzzy",
            controls=["x"],
            running_variable="score",
            cutoff=0.0,
            model="fuzzy-rdd",
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
    run_parser.add_argument("--weights")
    run_parser.add_argument("--cluster")
    run_parser.add_argument("--entity-id")
    run_parser.add_argument("--time-id")
    run_parser.add_argument("--treat-group")
    run_parser.add_argument("--post")
    run_parser.add_argument("--running-variable")
    run_parser.add_argument("--cutoff", type=float)
    run_parser.add_argument("--bandwidth", type=float)
    run_parser.add_argument("--kernel", choices=["uniform", "triangle", "epanechnikov"], default="triangle")
    run_parser.add_argument("--rdd-mode", choices=["auto", "local-linear", "global-poly"], default="auto")
    run_parser.add_argument("--poly-order", type=int, default=1)
    run_parser.add_argument("--estimand", choices=["ATE", "ATT"], default="ATE")
    run_parser.add_argument("--matched-num", type=int, default=1)
    run_parser.add_argument("--cov-type", choices=["auto", "robust", "cluster", "cluster-both", "hac"], default="auto")
    run_parser.add_argument("--hac-maxlags", type=int, default=1)
    run_parser.add_argument("--export-balance")
    run_parser.add_argument("--export-terms")
    run_parser.add_argument("--model", choices=["auto", "ols", "fe", "iv", "did", "event-study", "psm", "ipw", "aipw", "ipwra", "rdd", "fuzzy-rdd"], default="auto")
    run_parser.add_argument("--lead-window", type=int, default=4)
    run_parser.add_argument("--lag-window", type=int, default=3)
    run_parser.add_argument("--save-summary")

    demo_parser = subparsers.add_parser("demo", help="Generate demo data and run OLS/FE/DID/Event-Study/PSM/IPW/AIPW/IPWRA/RDD/Fuzzy-RDD/IV examples.")
    demo_parser.add_argument("--output-dir", default="lite_demo_output")

    knowledge_parser = subparsers.add_parser("knowledge", help="Print the absorbed econometric knowledge cards.")
    knowledge_parser.add_argument("--model", choices=["all", "ols", "fe", "iv", "did", "event-study", "psm", "ipw", "aipw", "ipwra", "rdd", "fuzzy-rdd"], default="all")

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
        weights=args.weights,
        cluster=args.cluster,
        entity_id=args.entity_id,
        time_id=args.time_id,
        treat_group=args.treat_group,
        post=args.post,
        running_variable=args.running_variable,
        cutoff=args.cutoff,
        bandwidth=args.bandwidth,
        kernel=args.kernel,
        rdd_mode=args.rdd_mode,
        poly_order=args.poly_order,
        estimand=args.estimand,
        matched_num=args.matched_num,
        cov_type=args.cov_type,
        hac_maxlags=args.hac_maxlags,
        export_balance=args.export_balance,
        export_terms=args.export_terms,
        model=args.model,
        lead_window=args.lead_window,
        lag_window=args.lag_window,
        save_summary=args.save_summary,
    )
    LiteEconometricsAgent(spec).run()


if __name__ == "__main__":
    main()
