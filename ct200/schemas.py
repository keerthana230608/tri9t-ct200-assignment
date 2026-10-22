from pydantic import BaseModel, Field, field_validator


class TestCase(BaseModel):
    title: str = Field(min_length=5, max_length=160)
    source_node_key: str = Field(min_length=1)
    rationale: str = Field(min_length=10, max_length=600)
    preconditions: list[str] = Field(min_length=1, max_length=8)
    steps: list[str] = Field(min_length=2, max_length=12)
    expected_result: str = Field(min_length=8, max_length=600)
    risk: str = Field(pattern="^(safety|functional|data|usability)$")

    @field_validator("steps", "preconditions")
    @classmethod
    def no_blank_items(cls, values: list[str]) -> list[str]:
        if any(not item.strip() for item in values):
            raise ValueError("items must not be blank")
        return values


class TestCaseSet(BaseModel):
    test_cases: list[TestCase] = Field(min_length=3, max_length=5)


class SelectionCreate(BaseModel):
    version_id: str
    node_keys: list[str] = Field(min_length=1, max_length=20)


class GenerateRequest(BaseModel):
    count: int = Field(default=3, ge=3, le=5)
    use_live_llm: bool = False


class IngestRequest(BaseModel):
    version_id: str
    pdf_path: str
