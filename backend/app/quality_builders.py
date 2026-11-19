"""Bounded internal Quality v1 views of one admitted evaluator snapshot."""

from collections import Counter
from collections.abc import Callable, Sequence
from typing import TypeVar

from fastapi import HTTPException

from .patient_pages import russian_key
from .quality_pages import QualityContext, Selector, error
from .services.data_quality import QUALITY_CATEGORIES, QUALITY_RULE_CODES, SEVERITY_RANK

_CATEGORY_ORDER = {category: i for i, category in enumerate(QUALITY_CATEGORIES)}
_CODE_ORDER = {code: i for i, code in enumerate(QUALITY_RULE_CODES)}
_NO_POSITION = -1
_T = TypeVar('_T')


def _page(
    context: QualityContext, selector: Selector, rows: Sequence[_T], render: Callable[[_T], dict],
) -> dict:
    offset = context.offset if selector == context.parsed.selector else 0
    limit = context.parsed.limit
    total = len(rows)
    if offset > total:
        error('PATIENT_QUERY_CHANGED', 409)
    end = min(offset + limit, total)
    has_next = end < total
    return {
        'items': [render(row) for row in rows[offset:end]],
        'page': {
            'limit': limit,
            'offset': offset,
            'total': total,
            'has_next': has_next,
            'next_cursor': context.cursor_for(selector, end) if has_next else None,
        },
    }


def _identity(row: dict) -> dict:
    return row


class _Projection:
    def __init__(self, context: QualityContext):
        self.context = context
        self.source = context.source
        self.quality = context.quality
        self.patients = {row['patient_id']: row for row in self.quality['patients']}
        if self.patients.keys() != self.source.doctor_by_patient.keys():
            error('QUALITY_READ_UNAVAILABLE', 503)
        self.selected, self.finding_count = self._matched_patients()
        self.groups = {
            group_id: count for group_id, group in context.source_groups.items()
            if (count := sum(patient_id in self.selected for patient_id in group['patient_ids']))
        }

    def _matched_patients(self) -> tuple[set[str], int]:
        query = self.context.parsed.query
        issues = self.quality['issues']
        if not query.echo()['active_filters']:
            return set(self.patients), len(issues)

        matched = set()
        count = 0
        for issue in issues:
            if query.severity is not None and issue['severity'] != query.severity:
                continue
            if query.category is not None and issue['category'] != query.category:
                continue
            if query.code is not None and issue['code'] != query.code:
                continue
            patient_id = issue['patient_id']
            if query.patient_id is not None and patient_id != query.patient_id:
                continue
            doctor = self.source.doctor_by_patient[patient_id]
            if query.doctor_user_id is not None and doctor != query.doctor_user_id:
                continue
            if query.department_id is not None and not any(
                link.department_id == query.department_id
                for link in self.source.departments_by_doctor.get(doctor, ())
            ):
                continue
            if query.search is not None and query.search not in self.patients[patient_id]['patient_name'].lower():
                continue
            matched.add(patient_id)
            count += 1
        return matched, count

    def _name_key(self, patient_id: str) -> tuple:
        return russian_key(self.patients[patient_id]['patient_name']), patient_id

    def _patient_order(self, patient_id: str) -> tuple:
        row = self.patients[patient_id]
        sort = self.context.parsed.query.sort
        name = self._name_key(patient_id)
        if sort == 'issues':
            return (-row['issue_count'], *name)
        completeness = row['completeness']['score']
        if sort == 'completeness':
            return (completeness, *name)
        return (SEVERITY_RANK[row['max_severity'] or 'info'], completeness, *name)

    def _patient_row(self, patient_id: str) -> dict:
        row = self.patients[patient_id]
        doctor = self.source.doctor_by_patient[patient_id]
        links = self.source.departments_by_doctor.get(doctor, ())
        ordered = sorted(links, key=lambda link: (russian_key(link.display_name), link.department_id))
        completeness = row['completeness']
        return {
            'patient_id': patient_id,
            'patient_name': row['patient_name'],
            'treating_doctor': {'user_id': doctor, 'display_name': row['treating_doctor_name']},
            'departments': {
                'preview': [
                    {'department_id': link.department_id, 'display_name': link.display_name}
                    for link in ordered[:3]
                ],
                'total': len(links),
                'has_more': len(links) > 3,
            },
            'completeness': {
                'score': completeness['score'],
                'filled': completeness['present'],
                'total': completeness['total'],
                'missing': completeness['missing'],
            },
            'issue_count': row['issue_count'],
            'max_severity': row['max_severity'],
            'top_issues': row['top_issues'],
            'can_edit': row['can_edit'],
        }

    def _group_row(self, group_id: str) -> dict:
        group = self.context.source_groups[group_id]
        return {
            'group_id': group_id,
            'reason_codes': group['reason_codes'],
            'reason': group['reason'],
            'member_total': len(group['patient_ids']),
            'matching_member_total': self.groups[group_id],
            'manual_review_required': True,
        }

    def _member_row(self, patient_id: str) -> dict:
        row = self.patients[patient_id]
        return {
            'patient_id': patient_id,
            'patient_name': row['patient_name'],
            'completeness_score': row['completeness']['score'],
            'matches_query': patient_id in self.selected,
        }

    def _facet_page(self, selector: Selector) -> dict:
        assigned = Counter(
            doctor for doctor in self.source.doctor_by_patient.values() if doctor is not None
        )
        if selector.facet == 'doctors':
            counts = assigned
            names = self.source.doctor_names
            field = 'user_id'
        else:
            counts = Counter()
            department_names = {}
            for doctor, count in assigned.items():
                for link in self.source.departments_by_doctor.get(doctor, ()):
                    counts[link.department_id] += count
                    department_names[link.department_id] = link.display_name
            names = department_names
            field = 'department_id'

        options = counts.keys()
        owner = selector.owner_patient_id
        if owner is not None:
            doctor = self.source.doctor_by_patient[owner]
            options = [link.department_id for link in self.source.departments_by_doctor.get(doctor, ())]
        if any(option not in names for option in options):
            error('QUALITY_READ_UNAVAILABLE', 503)
        search = selector.facet_search
        visible_options = sorted(
            (option for option in options if search is None or search in names[option].lower()),
            key=lambda option: (russian_key(names[option]), option),
        )

        def render(option: str) -> dict:
            return {field: option, 'display_name': names[option], 'patient_count': counts[option]}

        return _page(self.context, selector, visible_options, render)

    def _patient_findings(self, owner: str) -> list[dict]:
        rows = []
        for issue in self.quality['issues']:
            if issue['patient_id'] != owner:
                continue
            if issue['code'] == 'possible_duplicate':
                details = {'duplicate_group_count': len(issue['details']['group_ids'])}
            else:
                details = issue['details']
            row = {**issue, 'details': details}
            rows.append({**row, 'finding_key': self.context.finding_key(row)})

        def order(finding: dict) -> tuple:
            details = finding['details']
            return (
                SEVERITY_RANK[finding['severity']],
                _CATEGORY_ORDER[finding['category']],
                _CODE_ORDER[finding['code']],
                details.get('occurrence_index', _NO_POSITION),
                details.get('event_index', _NO_POSITION),
                finding['field'] or '',
                finding['finding_key'],
            )

        return sorted(rows, key=order)

    def _main(self) -> dict:
        return {
            'patients': _page(
                self.context, self.context.parsed.selector, sorted(self.selected, key=self._patient_order),
                self._patient_row,
            ),
            'duplicate_groups': _page(self.context, Selector('groups'), list(self.groups), self._group_row),
            'facets': {
                'doctors': self._facet_page(Selector('facets', facet='doctors')),
                'departments': self._facet_page(Selector('facets', facet='departments')),
            },
            'rules': {
                'version': 'quality-rules-v1',
                'count': len(self.quality['rules']),
                'descriptions': self.quality['rules'],
            },
        }

    def response(self) -> dict:
        selector = self.context.parsed.selector
        view = selector.view
        bases = {
            'scope_summary': 'authorized_scope',
            'totals': 'matched_query',
            'facets': 'authorized_scope',
            'groups': 'groups_touching_at_least_one_matched_patient',
        }
        common = {
            'bases': bases,
            'totals': {
                'patients': len(self.selected),
                'findings': self.finding_count,
                'duplicate_groups': len(self.groups),
            },
        }
        if view == 'page':
            return {**common, **self._main()}

        selection = {
            'group_id': selector.group_id,
            'facet': selector.facet,
            'owner_patient_id': selector.owner_patient_id,
        }
        if view == 'groups':
            owner = selector.owner_patient_id
            groups = [
                group_id for group_id in self.groups
                if owner is None or owner in self.context.source_groups[group_id]['patient_ids']
            ]
            collection = _page(self.context, selector, groups, self._group_row)
            return {**common, 'selection': selection, 'collection': collection}
        if view == 'members':
            group_id = selector.group_id
            if group_id not in self.groups:
                raise HTTPException(404, 'Not found')
            group = self.context.source_groups[group_id]
            members = sorted(group['patient_ids'], key=self._name_key)
            collection = _page(self.context, selector, members, self._member_row)
            bases['collection'] = 'all_authorized_members_of_selected_group'
            return {**common, 'selection': selection, 'group': self._group_row(group_id), 'collection': collection}
        if view == 'facets':
            return {**common, 'selection': selection, 'collection': self._facet_page(selector)}
        if view == 'patient_issues':
            owner = selector.owner_patient_id
            findings = self._patient_findings(owner)
            bases['collection'] = 'all_rules_of_owner_patient'
            return {
                **common,
                'selection': selection,
                'detail_scope': 'all_patient_rules',
                'patient': self._patient_row(owner),
                'collection': _page(self.context, selector, findings, _identity),
            }
        error()


def build(context: QualityContext) -> dict:
    """Project a bounded response without reimplementing clinical evaluation."""
    return _Projection(context).response()
