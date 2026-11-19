"""Strict wire shapes for Quality v1."""
from __future__ import annotations

from typing import Annotated, Generic, Literal, TypeVar, Union, get_args, get_origin
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

Id = Annotated[str, Field(min_length=1, max_length=100)]
Name = Annotated[str, Field(max_length=200)]
Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Token = Annotated[str, Field(min_length=1, max_length=8192)]
Count = Annotated[int, Field(ge=0, le=2**63-1)]
Severity = Literal['critical', 'warning', 'info']
Category = Literal['assignment', 'checklist', 'completeness', 'dates', 'duplicates', 'format', 'stage']
Code = Literal['missing_treating_doctor', 'missing_required_field', 'missing_policy_number',
               'missing_diagnosis', 'missing_stage', 'invalid_phone', 'actual_date_in_future',
               'stage_sequence_violation', 'overdue_event', 'incomplete_required_checklist', 'possible_duplicate']


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)

    @model_validator(mode='before')
    @classmethod
    def strict_literal_types(cls, value):
        if isinstance(value, dict):
            for name, field in cls.model_fields.items():
                if name in value and get_origin(field.annotation) is Literal:
                    if not any(type(value[name]) is type(expected) and value[name] == expected for expected in get_args(field.annotation)):
                        raise ValueError('invalid literal type')
        return value


class Snapshot(StrictDTO):
    token: Token
    revision: Digest
    query_hash: Digest
    computed_at: Annotated[str, Field(max_length=40)]
    institution_day: Annotated[str, Field(pattern=r'^\d{4}-\d{2}-\d{2}$')]
    timezone: Literal['Europe/Moscow']
    expires_at: Annotated[str, Field(max_length=40)]

    @field_validator('computed_at', 'expires_at')
    @classmethod
    def aware_time(cls, value):
        if datetime.fromisoformat(value).tzinfo is None:
            raise ValueError('naive snapshot time')
        return value

    @field_validator('institution_day')
    @classmethod
    def calendar_day(cls, value):
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError('invalid calendar day')
        return value


class Page(StrictDTO):
    limit: Annotated[int, Field(ge=1, le=100)]
    offset: Count
    total: Count
    has_next: bool
    next_cursor: Token | None


T = TypeVar('T')


class Collection(StrictDTO, Generic[T]):
    items: Annotated[list[T], Field(max_length=100)]
    page: Page

    @model_validator(mode='after')
    def exact_page(self):
        p = self.page
        if p.offset > p.total or len(self.items) != min(p.limit, p.total-p.offset):
            raise ValueError('invalid page size')
        more = p.offset+len(self.items) < p.total
        if p.has_next != more or (p.next_cursor is not None) != more:
            raise ValueError('invalid next page')
        return self


class Summary(StrictDTO):
    total_patients: Count
    patients_with_issues: Count
    total_issues: Count
    critical_count: Count
    warning_count: Count
    info_count: Count
    possible_duplicate_groups: Count
    unresolved_patients: Count
    average_completeness: Annotated[int, Field(ge=0, le=100)]

    @model_validator(mode='after')
    def exact_counts(self):
        if self.patients_with_issues > self.total_patients or self.unresolved_patients > self.total_patients:
            raise ValueError('invalid summary count')
        if self.critical_count+self.warning_count+self.info_count != self.total_issues:
            raise ValueError('invalid finding count')
        return self


class Totals(StrictDTO):
    patients: Count
    findings: Count
    duplicate_groups: Count


class QueryEcho(StrictDTO):
    sort: Literal['severity', 'issues', 'completeness']
    has_search: bool
    active_filters: Annotated[list[Literal['severity', 'category', 'code', 'patient_id',
                                         'doctor_user_id', 'department_id', 'search']], Field(max_length=7)]

    @model_validator(mode='after')
    def distinct_keys(self):
        if len(self.active_filters) != len(set(self.active_filters)):
            raise ValueError('duplicate filter')
        return self


class Bases(StrictDTO):
    scope_summary: Literal['authorized_scope']
    totals: Literal['matched_query']
    facets: Literal['authorized_scope']
    groups: Literal['groups_touching_at_least_one_matched_patient']
    collection: Literal['all_authorized_members_of_selected_group', 'all_rules_of_owner_patient'] | None = None


class Department(StrictDTO):
    department_id: Id
    display_name: Name


class DepartmentPreview(StrictDTO):
    preview: Annotated[list[Department], Field(max_length=3)]
    total: Count
    has_more: bool

    @model_validator(mode='after')
    def exact_preview(self):
        if len(self.preview) != min(3, self.total) or self.has_more != (self.total > len(self.preview)):
            raise ValueError('invalid department preview')
        return self


class Doctor(StrictDTO):
    user_id: Id | None
    display_name: Name


class Missing(StrictDTO):
    field: Literal['name', 'phone', 'policyNumber', 'diagnosis', 'stage', 'treatingDoctorUserId']
    label: Name
    severity: Severity


class Completeness(StrictDTO):
    score: Annotated[int, Field(ge=0, le=100)]
    filled: Annotated[int, Field(ge=0, le=6)]
    total: Literal[6]
    missing: Annotated[list[Missing], Field(max_length=6)]

    @model_validator(mode='after')
    def exact_missing(self):
        if self.filled+len(self.missing) != self.total or len({m.field for m in self.missing}) != len(self.missing):
            raise ValueError('invalid completeness')
        return self


class TopIssue(StrictDTO):
    code: Code
    severity: Severity
    title: Annotated[str, Field(max_length=256)]


class PatientRow(StrictDTO):
    patient_id: Id
    patient_name: Name
    treating_doctor: Doctor
    departments: DepartmentPreview
    completeness: Completeness
    issue_count: Count
    max_severity: Severity | None
    top_issues: Annotated[list[TopIssue], Field(max_length=3)]
    can_edit: bool

    @model_validator(mode='after')
    def exact_top(self):
        if len(self.top_issues) != min(3, self.issue_count) or (self.max_severity is None) != (self.issue_count == 0):
            raise ValueError('invalid issue summary')
        return self


class GroupRow(StrictDTO):
    group_id: Digest
    reason_codes: Annotated[list[Literal['policy', 'phone', 'name_age', 'name_diagnosis', 'name_nosology']], Field(min_length=1, max_length=5)]
    reason: Annotated[str, Field(max_length=2048)]
    member_total: Annotated[Count, Field(ge=2)]
    matching_member_total: Annotated[Count, Field(ge=1)]
    manual_review_required: Literal[True]

    @model_validator(mode='after')
    def exact_members(self):
        if self.matching_member_total > self.member_total or len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError('invalid group count')
        return self


class MemberRow(StrictDTO):
    patient_id: Id
    patient_name: Name
    completeness_score: Annotated[int, Field(ge=0, le=100)]
    matches_query: bool


class DoctorOption(StrictDTO):
    user_id: Id
    display_name: Name
    patient_count: Count


class DepartmentOption(StrictDTO):
    department_id: Id
    display_name: Name
    patient_count: Count


class Facets(StrictDTO):
    doctors: Collection[DoctorOption]
    departments: Collection[DepartmentOption]


class Rules(StrictDTO):
    version: Literal['quality-rules-v1']
    count: Literal[11]
    descriptions: dict[Code, Annotated[str, Field(max_length=4096)]]

    @model_validator(mode='after')
    def exact_rules(self):
        from .services.data_quality import QUALITY_RULE_CODES
        if set(self.descriptions) != set(QUALITY_RULE_CODES):
            raise ValueError('invalid rule dictionary')
        return self


class Selection(StrictDTO):
    group_id: Digest | None = None
    facet: Literal['doctors', 'departments'] | None = None
    owner_patient_id: Id | None = None


class Action(StrictDTO):
    label: Annotated[str, Field(max_length=256)]
    tab: Literal['overview', 'notifications', 'data', 'comments', 'events', 'checklist', 'notes', 'history', 'structure']
    can_write: bool
    workflow: Literal['unresolved_treating_doctor'] | None = None


class FindingDetails(StrictDTO):
    field_label: Name | None = None
    unresolved: bool | None = None
    normalized_digits: Count | None = None
    current_stage: Annotated[str, Field(max_length=80)] | None = None
    last_history_stage: Annotated[str, Field(max_length=80)] | None = None
    occurrence_index: Count | None = None
    event_index: Count | None = None
    stage: Annotated[str, Field(max_length=80)] | None = None
    planned_date: Annotated[str, Field(max_length=10)] | None = None
    days_overdue: Count | None = None
    missing_count: Count | None = None
    total_required: Count | None = None
    sample_missing: Annotated[list[Name], Field(max_length=3)] | None = None
    duplicate_group_count: Count | None = None


class Finding(StrictDTO):
    finding_key: Digest
    patient_id: Id
    code: Code
    category: Category
    severity: Severity
    title: Annotated[str, Field(max_length=256)]
    description: Annotated[str, Field(max_length=4096)]
    field: Annotated[str, Field(max_length=100)] | None
    details: FindingDetails
    suggested_action: Action


class Envelope(StrictDTO):
    mode: Literal['quality-v1']
    snapshot: Snapshot
    query: QueryEcho
    scope_summary: Summary
    totals: Totals
    bases: Bases


class MainResponse(Envelope):
    view: Literal['page']
    patients: Collection[PatientRow]
    duplicate_groups: Collection[GroupRow]
    facets: Facets
    rules: Rules


class GroupsResponse(Envelope):
    view: Literal['groups']
    selection: Selection
    collection: Collection[GroupRow]


class MembersResponse(Envelope):
    view: Literal['members']
    selection: Selection
    group: GroupRow
    collection: Collection[MemberRow]


class FacetResponse(Envelope):
    view: Literal['facets']
    selection: Selection
    collection: Collection[DoctorOption] | Collection[DepartmentOption]

    @model_validator(mode='after')
    def correct_facet(self):
        cls = DoctorOption if self.selection.facet == 'doctors' else DepartmentOption
        if self.selection.facet is None or any(not isinstance(item, cls) for item in self.collection.items):
            raise ValueError('invalid facet selection')
        return self


class PatientIssuesResponse(Envelope):
    view: Literal['patient_issues']
    selection: Selection
    detail_scope: Literal['all_patient_rules']
    patient: PatientRow
    collection: Collection[Finding]


ResponseDTO = Annotated[Union[MainResponse, GroupsResponse, MembersResponse, FacetResponse, PatientIssuesResponse], Field(discriminator='view')]
