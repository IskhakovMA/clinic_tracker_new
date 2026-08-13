# Patient Attention Workflow

## Purpose

Workflow "Пациент требует внимания" records a human decision that a patient needs additional control. It is separate from automatic overdue signals and Data Quality findings: automatic signals can help employees notice a situation, but they do not create persistent attention cases by themselves.

## Domain Model

The source of truth is `patient_attention_issues`.

One patient can have multiple attention issues over time. A patient is considered requiring attention when at least one issue has status `open`.

Fields:

- `id`
- `patient_id`
- `reason_code`
- `status`: `open` or `closed`
- `comment`
- `event_date`
- `created_at`
- `created_by_user_id`
- `closed_at`
- `closed_by_user_id`
- `resolution_comment`

The table stores only references and workflow data. It does not copy patient name, phone, OMS policy number, diagnosis, doctor name, or department name.

## Reasons

Stable backend reason codes and UI labels:

- `emergency_hospitalization`: Экстренная госпитализация
- `route_change`: Изменение маршрута пациента
- `critical_overdue`: Критичная просрочка
- `doctor_decision_required`: Требуется решение врача
- `department_head_decision_required`: Требуется решение заведующего
- `organizational_issue`: Организационная проблема
- `documents_or_data_issue`: Проблема с документами или данными
- `patient_unavailable`: Не удалось связаться / пациент временно недоступен
- `other`: Другая причина

The catalog is not a medical classifier and does not store hospitalization diagnosis or clinical conclusions.

## Lifecycle

Issues are created as `open`. Closing an issue sets `closed_at`, `closed_by_user_id`, and an optional `resolution_comment`. Closed issues remain in patient history and are not deleted by the regular UI.

Closing one issue does not clear patient-level attention while other open issues remain.

## Emergency Hospitalization

`emergency_hospitalization` is an explicit attention reason. Its `event_date` is required by the backend and shown in the patient card. The Attention Issue remains the source of truth for this workflow, so the first version does not create a separate patient event that could drift from the issue.

`event_date` is date-only (`YYYY-MM-DD`) and is validated server-side.

## API

- `GET /api/patients/attention/reasons`
- `GET /api/patients/{patient_id}/attention`
- `POST /api/patients/{patient_id}/attention`
- `POST /api/patients/{patient_id}/attention/{issue_id}/close`

Write endpoints require an active session, CSRF token, strict Pydantic request schemas, and patient write scope. GET endpoints do not change state.

## RBAC And Scope

The workflow uses existing roles and patient scope:

- doctor: scoped to assigned patients
- department head: scoped to department patients
- deputy chief oncology and CAOP head: global read-only visibility
- developer: administrative policy

Out-of-scope patient IDs and issue IDs return 404 so object existence is not disclosed. Visible but read-only users receive 403 for create/close.

## Notification Center

Active attention issues produce internal notification conditions of type `attention` for users who can see the patient. Stable identity is based on the attention issue id:

`attention:issue:{issue_id}`

Reading a notification does not close the issue. When the issue is closed, the condition resolves and the active notification disappears.

Notification cards show the reason label but not free-text comments.

## Analytics

Analytics V2 keeps separate semantics:

- `Явно требуют внимания`: patients with at least one active manual Attention Issue.
- `Операционные сигналы`: automatic overdue, checklist, or Data Quality signals.

The "Причины внимания" breakdown aggregates active issues by `reason_code`. The attention KPI counts each patient once even if the patient has multiple active issues.

Existing analytics filters continue to apply patient scope. `attention_reason` can narrow dashboard and details to patients with a specific active reason.

## Data Quality And Overdue Distinction

Data Quality findings remain data problems. Overdue events remain automatic operational signals. Neither creates an Attention Issue automatically in this version.

An employee can manually create an Attention Issue with `documents_or_data_issue` or `critical_overdue` when the situation needs persistent human control.

## Privacy And Audit

Attention comments and resolution comments may contain sensitive context. They are not written to audit, Analytics, notification stable keys, URLs, or generic server logs.

Audit events record only technical workflow facts such as action type, actor context, patient id, issue id, result, and timestamp.

## Performance

Patient lists use bulk attention summaries rather than querying each patient separately. Analytics aggregations and notification conditions are computed server-side using scoped patient sets and indexed attention fields.

Relevant indexes:

- `idx_attention_patient_status`
- `idx_attention_status`
- `idx_attention_reason`
- `idx_attention_created_at`

## Known Limitations

- No automatic escalation rules.
- No merge/dismiss/false-positive workflow.
- No real SMS, email, push, scheduler, or AI integration.
- No clinical diagnosis modeling for emergency hospitalization.

## Future AI Compatibility

Future AI tools should use scoped backend APIs, not direct SQL. The current model supports read-only questions such as which accessible patients require attention, why they were marked, how many open issues exist, and which issues are already closed.
