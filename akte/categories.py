"""Compatibility helpers for the project-specific settings page."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from .models import Category


def default_category(project, kind):
    if not kind:return None
    names={'wartung':['wartung','wartungen'],'umbau':['umbau','umbauten'],
           'reparatur':['reparatur','reparaturen'],'notiz':['notiz','notizen']}
    # Semantic name wins; a broad legacy default_kind is not a category match.
    return Category.objects.filter(project=project,name_key__in=names.get(kind,[])).first()


@login_required
def manage_categories(request):
    if request.method=='POST':
        from .settings_views import settings_view
        data=request.POST.copy();identity=data.get('category_id') or data.get('object_id')
        if identity:
            category=Category.objects.filter(pk=identity).first()
            if category:
                category=get_object_or_404(Category,pk=identity,owner=request.user)
                data['project']=str(category.project_id)
        data['scope']='category';data['object_id']=identity or ''
        request.POST=data
        return settings_view(request)
    project=request.GET.get('project','')
    return redirect(reverse('settings')+('?project='+project if project else ''))
