from datetime import date
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from tasks import idempotency
from tasks.ai import break_down_task, parse_task_text
from tasks.models import Task


def assert_error_shape(testcase, response, status, code=None):
    testcase.assertEqual(response.status_code, status)
    body = response.json()
    testcase.assertEqual(list(body.keys()), ["error"])
    error = body["error"]
    testcase.assertEqual(set(error.keys()), {"code", "message", "details"})
    testcase.assertIsInstance(error["code"], str)
    testcase.assertIsInstance(error["message"], str)
    testcase.assertIsInstance(error["details"], list)
    for item in error["details"]:
        testcase.assertEqual(set(item.keys()), {"field", "issue"})
    if code:
        testcase.assertEqual(error["code"], code)
    return error


def make_task(**overrides):
    data = {"title": "Pay rent", "description": "Monthly rent", "priority": "high"}
    data.update(overrides)
    return Task.objects.create(**data)


class BaseCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        idempotency.clear()


class CrudAndValidationTests(BaseCase):
    def test_create_and_defaults(self):
        r = self.client.post("/api/tasks/", {"title": "A", "description": "B"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["priority"], "medium")
        self.assertFalse(r.json()["completed"])
        self.assertIsNone(r.json()["due_date"])

    def test_empty_title_and_description(self):
        r = self.client.post("/api/tasks/", {"title": "  ", "description": ""}, format="json")
        error = assert_error_shape(self, r, 400, "VALIDATION_ERROR")
        self.assertIn({"field": "title", "issue": "must not be empty"}, error["details"])
        self.assertIn({"field": "description", "issue": "must not be empty"}, error["details"])

    def test_missing_fields(self):
        r = self.client.post("/api/tasks/", {}, format="json")
        error = assert_error_shape(self, r, 400)
        self.assertIn({"field": "title", "issue": "is required"}, error["details"])

    def test_completed_must_be_boolean(self):
        for bad in ["yes", "true", 1, "no", None]:
            r = self.client.post(
                "/api/tasks/", {"title": "A", "description": "B", "completed": bad}, format="json"
            )
            assert_error_shape(self, r, 400)
        self.assertEqual(Task.objects.count(), 0)

    def test_impossible_and_badly_formatted_dates(self):
        for bad in ["2026-02-30", "2026-13-01", "01-02-2026", "2026-2-3", "tomorrow", 20260101, ""]:
            r = self.client.post(
                "/api/tasks/", {"title": "A", "description": "B", "due_date": bad}, format="json"
            )
            assert_error_shape(self, r, 400)

    def test_valid_and_null_date(self):
        r = self.client.post("/api/tasks/", {"title": "A", "description": "B", "due_date": "2026-12-31"}, format="json")
        self.assertEqual(r.status_code, 201)
        r = self.client.post("/api/tasks/", {"title": "A", "description": "B", "due_date": None}, format="json")
        self.assertEqual(r.status_code, 201)

    def test_priority_choices(self):
        r = self.client.post("/api/tasks/", {"title": "A", "description": "B", "priority": "urgent"}, format="json")
        error = assert_error_shape(self, r, 400)
        self.assertEqual(error["details"][0]["field"], "priority")
        r = self.client.post("/api/tasks/", {"title": "A", "description": "B", "priority": "HIGH"}, format="json")
        assert_error_shape(self, r, 400)

    def test_body_not_an_object_and_bad_json(self):
        r = self.client.post("/api/tasks/", [1, 2], format="json")
        assert_error_shape(self, r, 400)
        r = self.client.post("/api/tasks/", "{not json", content_type="application/json")
        assert_error_shape(self, r, 400)

    def test_retrieve_update_delete_404_shape(self):
        for call in (
            lambda: self.client.get("/api/tasks/999/"),
            lambda: self.client.put("/api/tasks/999/", {"title": "A", "description": "B"}, format="json"),
            lambda: self.client.patch("/api/tasks/999/", {"title": "A"}, format="json"),
            lambda: self.client.delete("/api/tasks/999/"),
            lambda: self.client.get("/api/tasks/abc/"),
        ):
            assert_error_shape(self, call(), 404, "NOT_FOUND")

    def test_put_patch_delete_happy_path(self):
        task = make_task()
        r = self.client.put(f"/api/tasks/{task.id}/", {"title": "New", "description": "D"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["title"], "New")
        r = self.client.patch(f"/api/tasks/{task.id}/", {"completed": True}, format="json")
        self.assertTrue(r.json()["completed"])
        self.assertEqual(r.json()["title"], "New")
        r = self.client.patch(f"/api/tasks/{task.id}/", {"title": ""}, format="json")
        assert_error_shape(self, r, 400)
        r = self.client.delete(f"/api/tasks/{task.id}/")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(Task.objects.count(), 0)

    def test_method_not_allowed_uses_shape(self):
        r = self.client.put("/api/tasks/", {}, format="json")
        assert_error_shape(self, r, 405)

    def test_parent_cannot_be_set_through_main_endpoint(self):
        parent = make_task()
        r = self.client.post("/api/tasks/", {"title": "A", "description": "B", "parent": parent.id}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertIsNone(r.json()["parent"])


class ParserUnitTests(TestCase):
    today = date(2026, 11, 25)  # a Wednesday

    def test_rent_example(self):
        out = parse_task_text("Remind me to pay rent on the 1st of every month", self.today)
        self.assertEqual(out["title"], "Pay rent")
        self.assertEqual(out["due_date"], "2026-12-01")
        self.assertEqual(out["recurrence"], "monthly")

    def test_tomorrow(self):
        out = parse_task_text("Buy milk tomorrow", self.today)
        self.assertEqual((out["title"], out["due_date"]), ("Buy milk", "2026-11-26"))
        self.assertNotIn("recurrence", out)

    def test_weekday_and_weekly(self):
        out = parse_task_text("Team sync every monday", self.today)
        self.assertEqual((out["due_date"], out["recurrence"]), ("2026-11-30", "weekly"))
        out = parse_task_text("Call mom next friday", self.today)
        self.assertEqual(out["due_date"], "2026-11-27")

    def test_in_n_days_and_month_names(self):
        self.assertEqual(parse_task_text("Submit form in 3 days", self.today)["due_date"], "2026-11-28")
        self.assertEqual(parse_task_text("Visit doctor on 5 dec", self.today)["due_date"], "2026-12-05")
        self.assertEqual(parse_task_text("File taxes by march 3", self.today)["due_date"], "2027-03-03")

    def test_no_date(self):
        out = parse_task_text("Read a book", self.today)
        self.assertEqual((out["title"], out["due_date"]), ("Read a book", None))

    def test_impossible_date_is_passed_through_for_validation(self):
        out = parse_task_text("Party on 2026-02-30", self.today)
        self.assertEqual(out["due_date"], "2026-02-30")

    def test_empty_text_raises(self):
        with self.assertRaises(ValueError):
            parse_task_text("   ", self.today)


class FromTextTests(BaseCase):
    def test_creates_task(self):
        r = self.client.post("/api/tasks/from-text/", {"text": "Remind me to pay rent on the 1st of every month"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["title"], "Pay rent")
        self.assertEqual(r.json()["recurrence"], "monthly")
        self.assertEqual(Task.objects.count(), 1)

    def test_bad_input(self):
        for body in ({}, {"text": ""}, {"text": "   "}, {"text": 5}, {"text": None}, [1]):
            r = self.client.post("/api/tasks/from-text/", body, format="json")
            assert_error_shape(self, r, 400, "VALIDATION_ERROR")
        self.assertEqual(Task.objects.count(), 0)

    def test_parser_returns_empty_title_gives_502_and_saves_nothing(self):
        bad = {"title": "", "description": "x", "due_date": None}
        with mock.patch("tasks.views.parse_task_text", return_value=bad):
            r = self.client.post("/api/tasks/from-text/", {"text": "anything"}, format="json")
        assert_error_shape(self, r, 502, "INVALID_PARSER_OUTPUT")
        self.assertEqual(Task.objects.count(), 0)

    def test_parser_wrong_types_gives_502(self):
        for bad in (
            {"title": "T", "description": "D", "due_date": "2026-02-30"},
            {"title": "T", "description": "D", "priority": "urgent"},
            {"title": 123, "description": "D", "completed": "yes"},
            ["not", "a", "dict"],
            None,
        ):
            with mock.patch("tasks.views.parse_task_text", return_value=bad):
                r = self.client.post("/api/tasks/from-text/", {"text": "anything"}, format="json")
            assert_error_shape(self, r, 502)
        self.assertEqual(Task.objects.count(), 0)

    def test_parser_raises_gives_502(self):
        with mock.patch("tasks.views.parse_task_text", side_effect=RuntimeError("boom")):
            r = self.client.post("/api/tasks/from-text/", {"text": "anything"}, format="json")
        assert_error_shape(self, r, 502, "PARSER_ERROR")
        self.assertEqual(Task.objects.count(), 0)

    def test_impossible_date_from_text_is_502(self):
        r = self.client.post("/api/tasks/from-text/", {"text": "Party on 2026-02-30"}, format="json")
        assert_error_shape(self, r, 502)
        self.assertEqual(Task.objects.count(), 0)


class SubtaskTests(BaseCase):
    def test_create_and_list_subtasks(self):
        parent = make_task()
        r = self.client.post(f"/api/tasks/{parent.id}/subtasks/", {"title": "S1", "description": "d"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["parent"], parent.id)
        r = self.client.get(f"/api/tasks/{parent.id}/subtasks/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 1)

    def test_validation_and_404(self):
        parent = make_task()
        r = self.client.post(f"/api/tasks/{parent.id}/subtasks/", {"title": ""}, format="json")
        assert_error_shape(self, r, 400, "VALIDATION_ERROR")
        assert_error_shape(self, self.client.post("/api/tasks/999/subtasks/", {"title": "S", "description": "d"}, format="json"), 404)
        assert_error_shape(self, self.client.get("/api/tasks/999/subtasks/"), 404)
        assert_error_shape(self, self.client.post("/api/tasks/999/breakdown/", {}, format="json"), 404)

    def test_subtask_of_subtask_and_breakdown_of_subtask_are_400(self):
        parent = make_task()
        child = make_task(title="child", parent=parent)
        r = self.client.post(f"/api/tasks/{child.id}/subtasks/", {"title": "S", "description": "d"}, format="json")
        assert_error_shape(self, r, 400)
        r = self.client.post(f"/api/tasks/{child.id}/breakdown/", {}, format="json")
        assert_error_shape(self, r, 400)
        self.assertEqual(Task.objects.count(), 2)

    def test_cascade_delete(self):
        parent = make_task()
        make_task(title="c1", parent=parent)
        make_task(title="c2", parent=parent)
        self.client.delete(f"/api/tasks/{parent.id}/")
        self.assertEqual(Task.objects.count(), 0)

    def test_breakdown_split_and_template(self):
        parent = make_task(title="Buy milk, eggs and bread", due_date=date(2026, 12, 1))
        r = self.client.post(f"/api/tasks/{parent.id}/breakdown/", {}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual([t["title"] for t in r.json()], ["Buy milk", "Eggs", "Bread"])
        self.assertEqual(r.json()[0]["due_date"], "2026-12-01")
        other = make_task(title="Pay rent")
        r = self.client.post(f"/api/tasks/{other.id}/breakdown/", {}, format="json")
        self.assertEqual(len(r.json()), 3)

    def test_breakdown_one_bad_item_saves_none(self):
        parent = make_task()
        items = [
            {"title": "ok", "description": "d", "priority": "low", "due_date": None},
            {"title": "", "description": "d", "priority": "low", "due_date": None},
        ]
        with mock.patch("tasks.views.break_down_task", return_value=items):
            r = self.client.post(f"/api/tasks/{parent.id}/breakdown/", {}, format="json")
        assert_error_shape(self, r, 502)
        self.assertEqual(Task.objects.filter(parent=parent).count(), 0)

    def test_breakdown_function_raises_or_returns_junk(self):
        parent = make_task()
        for kwargs in ({"side_effect": RuntimeError("x")}, {"return_value": "junk"}, {"return_value": []}):
            with mock.patch("tasks.views.break_down_task", **kwargs):
                r = self.client.post(f"/api/tasks/{parent.id}/breakdown/", {}, format="json")
            assert_error_shape(self, r, 502)
        self.assertEqual(Task.objects.filter(parent=parent).count(), 0)

    def test_break_down_task_function_directly(self):
        items = break_down_task(make_task(title="Something odd"))
        self.assertEqual(len(items), 3)


class IdempotencyTests(BaseCase):
    def test_create_same_key_same_body_once(self):
        body = {"title": "A", "description": "B"}
        r1 = self.client.post("/api/tasks/", body, format="json", headers={"Idempotency-Key": "k1"})
        r2 = self.client.post("/api/tasks/", body, format="json", headers={"Idempotency-Key": "k1"})
        self.assertEqual((r1.status_code, r2.status_code), (201, 201))
        self.assertEqual(r1.json(), r2.json())
        self.assertEqual(Task.objects.count(), 1)

    def test_same_key_different_body_is_422(self):
        self.client.post("/api/tasks/", {"title": "A", "description": "B"}, format="json", headers={"Idempotency-Key": "k1"})
        r = self.client.post("/api/tasks/", {"title": "Z", "description": "B"}, format="json", headers={"Idempotency-Key": "k1"})
        assert_error_shape(self, r, 422)
        self.assertEqual(Task.objects.count(), 1)

    def test_key_order_in_body_does_not_matter(self):
        self.client.post("/api/tasks/", {"title": "A", "description": "B"}, format="json", headers={"Idempotency-Key": "k"})
        r = self.client.post("/api/tasks/", {"description": "B", "title": "A"}, format="json", headers={"Idempotency-Key": "k"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Task.objects.count(), 1)

    def test_failed_request_is_not_stored_so_client_can_fix_and_retry(self):
        r = self.client.post("/api/tasks/", {"title": "", "description": "B"}, format="json", headers={"Idempotency-Key": "k"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/tasks/", {"title": "Fixed", "description": "B"}, format="json", headers={"Idempotency-Key": "k"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Task.objects.count(), 1)

    def test_no_key_means_no_protection(self):
        body = {"title": "A", "description": "B"}
        self.client.post("/api/tasks/", body, format="json")
        self.client.post("/api/tasks/", body, format="json")
        self.assertEqual(Task.objects.count(), 2)

    def test_from_text_does_not_call_parser_again(self):
        headers = {"Idempotency-Key": "ft"}
        body = {"text": "Buy milk tomorrow"}
        self.client.post("/api/tasks/from-text/", body, format="json", headers=headers)
        with mock.patch("tasks.views.parse_task_text") as parser:
            r = self.client.post("/api/tasks/from-text/", body, format="json", headers=headers)
            parser.assert_not_called()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Task.objects.count(), 1)
        r = self.client.post("/api/tasks/from-text/", {"text": "different"}, format="json", headers=headers)
        assert_error_shape(self, r, 422)

    def test_from_text_502_is_not_stored(self):
        headers = {"Idempotency-Key": "ft2"}
        with mock.patch("tasks.views.parse_task_text", side_effect=RuntimeError):
            self.client.post("/api/tasks/from-text/", {"text": "x"}, format="json", headers=headers)
        r = self.client.post("/api/tasks/from-text/", {"text": "Buy milk tomorrow"}, format="json", headers=headers)
        self.assertEqual(r.status_code, 201)

    def test_breakdown_idempotent(self):
        parent = make_task(title="Buy milk, eggs")
        headers = {"Idempotency-Key": "bd"}
        r1 = self.client.post(f"/api/tasks/{parent.id}/breakdown/", {}, format="json", headers=headers)
        r2 = self.client.post(f"/api/tasks/{parent.id}/breakdown/", {}, format="json", headers=headers)
        self.assertEqual(r1.json(), r2.json())
        self.assertEqual(Task.objects.filter(parent=parent).count(), 2)


class ListingTests(BaseCase):
    def setUp(self):
        super().setUp()
        make_task(title="Pay rent", description="landlord", priority="high", due_date=date(2026, 12, 1))
        make_task(title="Buy milk", description="dairy", priority="low", due_date=date(2026, 11, 28), completed=True)
        make_task(title="Pay phone bill", description="telecom", priority="high", due_date=date(2026, 12, 5))
        make_task(title="Read book", description="novel", priority="medium")

    def titles(self, response):
        return [t["title"] for t in response.json()["results"]]

    def test_pagination_shape(self):
        r = self.client.get("/api/tasks/?page_size=2")
        self.assertEqual(r.json()["count"], 4)
        self.assertEqual(len(r.json()["results"]), 2)
        self.assertIsNotNone(r.json()["next"])

    def test_filters(self):
        self.assertEqual(len(self.client.get("/api/tasks/?priority=high").json()["results"]), 2)
        self.assertEqual(self.titles(self.client.get("/api/tasks/?completed=true")), ["Buy milk"])
        self.assertEqual(len(self.client.get("/api/tasks/?due_before=2026-12-01").json()["results"]), 2)
        self.assertEqual(len(self.client.get("/api/tasks/?due_after=2026-12-01").json()["results"]), 2)

    def test_search_ordering_pagination_together(self):
        r = self.client.get("/api/tasks/?priority=high&search=pay&ordering=-due_date&page_size=1&page=1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 2)
        self.assertEqual(self.titles(r), ["Pay phone bill"])
        r = self.client.get("/api/tasks/?priority=high&search=pay&ordering=-due_date&page_size=1&page=2")
        self.assertEqual(self.titles(r), ["Pay rent"])

    def test_bad_filter_and_bad_page_use_error_shape(self):
        assert_error_shape(self, self.client.get("/api/tasks/?due_before=nope"), 400)
        assert_error_shape(self, self.client.get("/api/tasks/?page=99"), 404)
        assert_error_shape(self, self.client.get("/api/tasks/?priority=urgent"), 400)

    def test_is_subtask_filter(self):
        parent = Task.objects.first()
        make_task(title="child", parent=parent)
        self.assertEqual(self.titles(self.client.get("/api/tasks/?is_subtask=true")), ["child"])
        self.assertEqual(len(self.client.get("/api/tasks/?is_subtask=false").json()["results"]), 4)
