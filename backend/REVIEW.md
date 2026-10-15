# REVIEW.md

Problems I found in the starter `app/main.py` and what I did about them.

| # | Where | What is wrong | How I would notice it | How I fixed it |
|---|---|---|---|---|
| 1 | Startup | No indexes were created, so nothing stopped duplicates and queries were slow. | Two requests at the same time create duplicate records. The explain endpoint shows a full collection scan. | Added `create_indexes()` on startup: unique `emp_code`, unique `(emp_code, date)`, and a few more for the common queries. |
| 2 | `compute_late_minutes` | The 10-minute grace was checked after cutting off the seconds, so 10 min 30 sec late counted as on time. | Punch in at 09:40:30 for a 09:30 shift gives 0. | Now it works in seconds: 600 seconds or less gives 0, otherwise `seconds // 60`. |
| 3 | `compute_late_minutes` | It used the server's local time, not IST. | Server in UTC: a 09:35 IST punch looks like an early punch. | Convert to IST first, then compare with the shift start in IST. |
| 4 | `compute_late_minutes` | Night shifts (like 22:00 to 06:00) were not handled. | A 01:00 punch gets compared with that same day's 22:00 start. | Added `shift_start_for_punch`. If the shift ends before it starts and the punch is early morning, use yesterday's shift start. |
| 5 | `compute_work_hours` | Python `round()` is not half-up and has float errors. | A 9 h 7 min 30 s shift gives the wrong second decimal. | Used `Decimal` with `ROUND_HALF_UP`. |
| 6 | `compute_overtime` | No 30-minute minimum, wrong for night shifts, and it ignored seconds. | Leaving 10 minutes after shift end shows 10 minutes overtime. | Added `shift_end_for_record`. Overtime is 0 if under 30 minutes, else `seconds // 60`. |
| 7 | Date checks (missing) | Dates and months were never validated. Python can accept formats like `20261015`. | `date_from=20261015` is accepted. | Added `validate_date_string`, `validate_month_string` and `parse_hhmm`. They return 422 on bad input. |
| 8 | Time format | Times were stored and returned as normal datetimes in server time, not epoch milliseconds in UTC. | Response shows date strings that change with the server timezone. | Added helpers to convert to and from epoch ms, and used `public_employee` and `public_attendance` for responses. |
| 9 | `EmployeeIn` | No validation at all. Bad code, empty name, bad email and bad times were all accepted. | `emp_code="abc"` and `shift_start="25:99"` still return 201. | Added patterns, length limits, time and date validators, `extra="forbid"`, and a check that shift start and end are different. |
| 10 | `PunchInIn` | `status` could be any text. `punched_at` could be a float or in seconds. | `status="FOO"` returns 201. | Status is limited to PRESENT, WFH, ON_DUTY. `punched_at` must be a strict int in epoch ms range. |
| 11 | `create_employee` | Checks for a duplicate first, then inserts, so two requests at once can both pass. `created_at` used local time. | Two parallel POSTs with the same code create two employees. | Use the unique index and catch `DuplicateKeyError` to return 409. `created_at` is saved in UTC and returned as epoch ms. |
| 12 | `list_employees` | `skip = page * page_size` skips page 1. `total` ignores the department filter. No sorting or limits. | `page=1` shows page 2 data. `total` is the whole collection. | `skip = (page - 1) * page_size`, count with the filter, sort by `emp_code`, and limit `page` and `page_size`. |
| 13 | `punch_in` | It never checked the employee exists. | Punch in for `EMP9999` gives a 500 error. | Return 404 "Employee not found". |
| 14 | `punch_in` | It used server local time, and `if body.punched_at` treats 0 as missing. | A 00:30 IST punch is saved on the wrong date. | Convert from UTC to IST for the date, and check `is None`. |
| 15 | `punch_in` | Night-shift early morning punches should belong to the previous day. | A 01:00 punch creates a record for the wrong day. | Subtract one day when it is an overnight shift and the time is before shift end. |
| 16 | `punch_in` | Duplicate check and insert are two steps, so there is a race. | Two punch-ins at the same time create two records. | Unique `(emp_code, date)` index and catch `DuplicateKeyError` to return 409. |
| 17 | `punch_in` response | Response had a raw datetime and the wrong format. | Response does not match the contract. | Return through `public_attendance`. |
| 18 | `list_attendance` | No validation of dates, status or paging. | `status=BANANA` returns 200 with an empty list. | Validate everything and return 422 for bad input. |
| 19 | `list_attendance` | It loaded every record into memory, then sorted and sliced in Python. | Slow on big data, and equal dates appear in random order. | Use `count_documents` and sort, skip and limit in the database (date desc, then emp_code). |
| 20 | `list_attendance` | Raw `_id` and datetimes were returned. | JSON output is messy or fails. | Hide `_id` and use `public_attendance`. |
| 21 | `/health` | Always says ok, even if MongoDB is down. | Stop Mongo and health still says ok. | Ping the database and return 503 on error. |
| 22 | `POST /attendance/punch-out` | Missing. | Returns 404 or 405. | Wrote it. It finds the open record, checks the time is positive and under 24 hours, calculates hours, overtime and half day, and updates atomically so two requests cannot both win (409). |
| 23 | `PATCH /attendance/{emp_code}/{date}` | Missing. | Returns 404 or 405. | Wrote it. Only given fields change, ABSENT and LEAVE cannot have punch times, derived fields are recalculated, real changes are saved in `history`, and a concurrent change returns 409. |
| 24 | `GET /analytics/employees/{emp_code}/monthly` | Missing. | Returns 404 or 405. | Wrote it with working days, present days, late days, overtime, hours, half days and attendance rate. |
| 25 | `GET /analytics/departments/summary` | Missing. | Returns 404 or 405. | Wrote it starting from the employees collection, so departments with no attendance still show. |
| 26 | `GET /analytics/leaderboard/late` | Missing. | Returns 404 or 405. | Wrote it with a fixed sort order, ranks that handle ties, and employee name and department. |
| 27 | `GET /analytics/departments/{department}/trend` | Missing. | Returns 404 or 405. | Wrote it with a 92-day limit and zero-filled days so there are no gaps. |
| 28 | `GET /admin/explain/{endpoint}` | Missing. | Returns 404 or 405. | Wrote it using `explain("executionStats")`, and it reports whether an index or a full scan is used. |

## Things I checked and decided were NOT defects

- **`IST` as a fixed +05:30 offset:** India has no daylight saving, so this is correct.
- **Dates stored as `YYYY-MM-DD` text:** text order is the same as date order, so range queries work and can use an index.
- **Paging response shape** (`items`, `total`, `page`, `page_size`): matches the contract.
- **Status codes 201 and 409:** they were right. Only the way duplicates were detected was wrong.