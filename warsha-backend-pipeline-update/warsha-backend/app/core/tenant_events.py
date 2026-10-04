"""
Tenant injection: automatically set tenant_id on every new ORM object
right before flush, based on the current request's ContextVar.

هذا يضمن أن أي كود يعمل db.add(SomeModel(...)) بدون tenant_id
يحصل على القيمة الصحيحة تلقائياً — حماية ضد النسيان البشري.
"""
from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from app.core.database import Base, current_tenant_id


# الموديلات اللي مالهاش tenant_id (لا يلزمها)
NO_TENANT_MODELS = {"tenants"}


# ── حماية القراءة: فلترة تلقائية بالـ tenant على كل SELECT ──────────────
# المشكلة: endpoints كتير بتعمل select(Model) من غير .where(tenant_id==...)
# والاعتماد كان على Postgres RLS — لكن مفيش RLS policies فعليًا، والـ desktop
# بيستخدم SQLite (مفيش RLS أصلًا) → تسريب بيانات بين الورش.
# الحل: نحقن شرط tenant_id تلقائيًا في كل استعلام ORM داخل سياق طلب فيه tenant،
# فيشتغل العزل على أي قاعدة بيانات (SQLite + Postgres) كدفاع متعدد الطبقات.

def _tenant_scoped_entities():
    """قائمة (مُخزّنة) بكل الموديلات اللي عندها عمود tenant_id."""
    global _TENANT_ENTITIES
    if _TENANT_ENTITIES is None:
        ents = []
        for mapper in Base.registry.mappers:
            cls = mapper.class_
            if hasattr(cls, "tenant_id"):
                ents.append(cls)
        _TENANT_ENTITIES = ents
    return _TENANT_ENTITIES


_TENANT_ENTITIES = None


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_filter(execute_state):
    # SELECT + UPDATE/DELETE الجماعي (update(Model)/delete(Model)).
    # إصلاح ثغرة عزل: الفلتر كان على SELECT بس، فأي UPDATE/DELETE جماعي شرطه
    # مش فيه tenant_id (مثلاً delete(X).where(X.invoice_id == iid) أو scheduler
    # الأقساط من طلب ورشة) كان بيعدّل/يمسح صفوف ورش تانية. with_loader_criteria
    # بيشتغل على UPDATE/DELETE في SQLAlchemy 2.0 فنطبّق نفس الشرط.
    if not (execute_state.is_select or execute_state.is_update or execute_state.is_delete):
        return
    # تجاهل إعادة تحميل عمود لكائن محمّل بالفعل
    if execute_state.is_select and execute_state.is_column_load:
        return
    # السماح بتخطّي الفلتر صراحةً (تقارير عابرة للمستأجرين / مهام إدارية)
    if execute_state.execution_options.get("skip_tenant_filter", False):
        return
    tid = current_tenant_id.get()
    if tid is None:
        # خارج سياق طلب (login/signup/migrations) — لا فلترة
        return
    # تعبير SQL مباشر لكل موديل (بدون lambda) → tid يمر كـ bound parameter
    opts = [
        with_loader_criteria(cls, cls.tenant_id == tid, include_aliases=True)
        for cls in _tenant_scoped_entities()
    ]
    if opts:
        execute_state.statement = execute_state.statement.options(*opts)


@event.listens_for(Session, "before_flush")
def _inject_tenant_id(session, flush_context, instances):
    """قبل أي flush، نتأكد أن كل صف جديد عنده tenant_id."""
    tid = current_tenant_id.get()
    if tid is None:
        # خارج سياق الطلب (migrations مثلاً) — اتركها للكود الصريح
        return

    for obj in session.new:
        # تجاهل الموديلات اللي مالهاش العمود ده
        tname = getattr(obj.__class__, "__tablename__", None)
        if tname in NO_TENANT_MODELS:
            continue
        if not hasattr(obj, "tenant_id"):
            continue
        # املأ القيمة فقط لو فاضية (نحترم لو الكود حدّدها صراحة)
        if getattr(obj, "tenant_id", None) is None:
            obj.tenant_id = tid
