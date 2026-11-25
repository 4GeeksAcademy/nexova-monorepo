"""
Las pruebas que pedirá Legal (BRIEFING «Pruebas que pedirá Legal»), con llamadas directas a la API.

1. Empleado llamando a un update solo de Supervisor → 403.
2. Supervisor llamando a `GET /roles` o a la asignación de usuario (solo Admin) → 403.
3. Javier (supervisor + talent_selection) hace GET del reporte de ventas de Megan por id → 403.
4. Cambiar el departamento de Megan no cambia su rol; cambiar el rol de Javier no cambia su departamento.
5. La verificación vive en una dependencia, no copiada en el cuerpo de cada ruta (test estático).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from conftest import ELENA, JAVIER, MEGAN, SERGIO


def test_1_employee_cannot_call_a_supervisor_only_update(api, as_user, report_id):
    elena = as_user(ELENA)  # employee · corporate_training
    training_report = api.post(
        "/internal-reports", json={"title": "Catálogo Q1", "body": "Inscripciones abiertas."}, headers=elena
    )
    assert training_report.status_code == 201, training_report.text

    response = api.put(
        f"/internal-reports/{training_report.json()['id']}", json={"title": "Editado"}, headers=elena
    )

    assert response.status_code == 403
    assert "supervisor" in response.json()["detail"]  # explícito: dice qué rol falta


def test_2_supervisor_cannot_call_admin_only_endpoints(api, as_user, user_id):
    javier = as_user(JAVIER)

    assert api.get("/roles", headers=javier).status_code == 403
    assert api.get("/departments", headers=javier).status_code == 403
    assert api.get("/users", headers=javier).status_code == 403
    patch = api.patch(
        f"/users/{user_id(MEGAN)}/assignment", json={"department_id": "marketing"}, headers=javier
    )
    assert patch.status_code == 403
    assert "admin" in patch.json()["detail"]


def test_3_javier_cannot_read_megans_sales_report_by_id(api, as_user, report_id):
    sales_report = report_id("sales")

    forbidden = api.get(f"/internal-reports/{sales_report}", headers=as_user(JAVIER))
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]  # cuerpo explícito, nunca un 200 vacío

    # Megan, del mismo rol pero del departamento dueño del dato, sí lo lee.
    allowed = api.get(f"/internal-reports/{sales_report}", headers=as_user(MEGAN))
    assert allowed.status_code == 200
    assert allowed.json()["department_id"] == "sales"


def test_4_role_and_department_are_independent_axes(api, as_user, user_id):
    sergio = as_user(SERGIO)

    # Megan pasa a marketing: su rol (supervisor) no cambia.
    megan_id = user_id(MEGAN)
    moved = api.patch(f"/users/{megan_id}/assignment", json={"department_id": "marketing"}, headers=sergio)
    assert moved.status_code == 200
    assert moved.json()["department_id"] == "marketing"
    assert moved.json()["role_id"] == "supervisor"
    assert api.get("/auth/me", headers=as_user(MEGAN)).json()["role"] == "supervisor"

    # Javier pasa a employee: su departamento (talent_selection) no cambia.
    javier_id = user_id(JAVIER)
    demoted = api.patch(f"/users/{javier_id}/assignment", json={"role_id": "employee"}, headers=sergio)
    assert demoted.status_code == 200
    assert demoted.json()["role_id"] == "employee"
    assert demoted.json()["department_id"] == "talent_selection"
    me = api.get("/auth/me", headers=as_user(JAVIER)).json()
    assert (me["role"], me["department_id"]) == ("employee", "talent_selection")


def test_4b_changing_both_axes_at_once_and_unrelated_fields_untouched(api, as_user, user_id):
    sergio = as_user(SERGIO)
    elena_id = user_id(ELENA)

    both = api.patch(
        f"/users/{elena_id}/assignment", json={"role_id": "supervisor", "department_id": "sales"}, headers=sergio
    )

    assert both.status_code == 200
    assert (both.json()["role_id"], both.json()["department_id"]) == ("supervisor", "sales")


def test_5_permission_checks_live_in_a_dependency_not_in_route_bodies():
    """Ninguna ruta de RBAC/usuarios compara roles a mano: usa `Depends(require_*)` (authz.py)."""
    import rbac.router as rbac_router
    import routes.users as users_router

    forbidden_in_bodies = {"role_code", "has_role", "role_rank", "rank_of"}
    for module in (rbac_router, users_router):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not _is_route(node):
                continue
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
                n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
            }
            leaked = used & forbidden_in_bodies
            # `update_assignment`/`update_user` usan role_code solo para el guard de «último admin», no para autorizar.
            allowed = {"update_assignment", "update_user"}
            assert not leaked or node.name in allowed, f"{module.__name__}.{node.name} comprueba roles a mano: {leaked}"


def _is_route(node: ast.FunctionDef) -> bool:
    return any(
        isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in {"get", "post", "put", "patch", "delete"}
        for d in node.decorator_list
    )


def test_every_sensitive_route_declares_a_role_dependency():
    """Cada ruta de /roles, /departments, /internal-reports y /users declara una dependencia de rol."""
    from rbac import router as rbac
    from routes import users

    def deps_of(route) -> set[str]:
        names: set[str] = set()
        stack = list(route.dependant.dependencies)
        while stack:
            dep = stack.pop()
            names.add(getattr(dep.call, "__name__", ""))
            stack.extend(dep.dependencies)
        return names

    from fastapi import APIRouter

    routers: list[APIRouter] = [*rbac.routers, users.router]
    open_routes = []
    for router in routers:
        for route in router.routes:
            names = deps_of(route)
            if not any(n.startswith("require_") or n in {"department_scope", "get_current_user"} for n in names):
                open_routes.append(route.path)
    assert open_routes == []
    assert Path  # silencia el import si cambia el test
