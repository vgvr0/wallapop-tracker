"""Typed input and output contracts for listing analysis."""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConditionAssessment(StrEnum):
    NEW = "new"
    LIKE_NEW = "like_new"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    UNKNOWN = "unknown"


class DefectType(StrEnum):
    COSMETIC_DAMAGE = "cosmetic_damage"
    FUNCTIONAL_ISSUE = "functional_issue"
    BATTERY_DEGRADATION = "battery_degradation"
    MISSING_ACCESSORY = "missing_accessory"
    MISSING_PARTS = "missing_parts"
    REPAIR_HISTORY = "repair_history"
    WEAR = "wear"
    OTHER = "other"


class Severity(StrEnum):
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"
    UNKNOWN = "unknown"


class RiskFlagType(StrEnum):
    SUSPICIOUS_PRICE = "suspicious_price"
    DESCRIPTION_TOO_SHORT = "description_too_short"
    DESCRIPTION_INCONSISTENT = "description_inconsistent"
    CONDITION_MISMATCH = "condition_mismatch"
    MODEL_MISMATCH = "model_mismatch"
    POSSIBLE_COUNTERFEIT = "possible_counterfeit"
    EXTERNAL_CONTACT_REQUEST = "external_contact_request"
    MISSING_INFORMATION = "missing_information"
    OTHER = "other"


class DealQuality(StrEnum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    UNKNOWN = "unknown"


class ListingAnalysisContext(BaseModel):
    """Listing data and optional, explicitly supplied comparison context."""

    model_config = ConfigDict(extra="forbid")

    listing_id: str
    title: str = ""
    description: str = ""
    price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    condition: str | None = None
    brand: str | None = None
    model: str | None = None
    category: str | None = None
    seller_rating: float | None = Field(default=None, ge=0, le=5)
    seller_reviews: int | None = Field(default=None, ge=0)
    seller_sales: int | None = Field(default=None, ge=0)
    market_median_price: Decimal | None = Field(default=None, ge=0)
    market_p25: Decimal | None = Field(default=None, ge=0)
    market_p75: Decimal | None = Field(default=None, ge=0)
    market_sample_size: int | None = Field(default=None, ge=0)
    previous_price: Decimal | None = Field(default=None, ge=0)
    days_observed: int | None = Field(default=None, ge=0)
    price_change_count: int | None = Field(default=None, ge=0)
    deterministic_deal_score: float | None = Field(default=None, ge=0, le=100)

    @field_validator(
        "title", "description", "currency", "condition", "brand", "model", "category", mode="before"
    )
    @classmethod
    def normalize_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @property
    def discount_vs_market_median(self) -> Decimal | None:
        """Return the fractional discount; market data is never inferred."""
        if self.price is None or self.market_median_price in (None, Decimal("0")):
            return None
        return (self.market_median_price - self.price) / self.market_median_price

    @property
    def has_market_context(self) -> bool:
        return self.market_median_price is not None or self.market_sample_size is not None


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: DefectType
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=500)


class RiskFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: RiskFlagType
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=500)


class ListingAIAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_assessment: ConditionAssessment
    condition_confidence: float = Field(ge=0, le=1)
    defects: list[EvidenceItem] = Field(default_factory=list)
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    positive_signals: list[str] = Field(default_factory=list, max_length=20)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    semantic_score: float = Field(ge=0, le=100)
    risk_score: float = Field(ge=0, le=100)
    deal_quality: DealQuality
    deal_confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1, max_length=1000)


class ListingAnalysisMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    prompt_version: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(ge=0)
    valid_response: bool = True


class ListingAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis: ListingAIAnalysis
    metadata: ListingAnalysisMetadata
