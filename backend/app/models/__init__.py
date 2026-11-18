"""Importing this package registers every model on ``Base.metadata`` (used by Alembic)."""

from app.models.audit import AuditLog
from app.models.diagnosis import Diagnosis, DiagnosisCase, DiagnosisHypothesis
from app.models.evaluation import (
    Dataset,
    DatasetCase,
    DatasetVersion,
    DatasetVersionCase,
    EvaluationResult,
    EvaluationRun,
)
from app.models.experiment import Experiment, ExperimentDecision
from app.models.project import Project, ProjectApiKey
from app.models.trace import Span, Trace
from app.models.user import OAuthIdentity, User, UserSession

__all__ = [
    "AuditLog",
    "Dataset",
    "DatasetCase",
    "DatasetVersion",
    "DatasetVersionCase",
    "Diagnosis",
    "DiagnosisCase",
    "DiagnosisHypothesis",
    "EvaluationResult",
    "EvaluationRun",
    "Experiment",
    "ExperimentDecision",
    "OAuthIdentity",
    "Project",
    "ProjectApiKey",
    "Span",
    "Trace",
    "User",
    "UserSession",
]
