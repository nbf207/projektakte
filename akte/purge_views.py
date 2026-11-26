from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from .models import Document, Entry, PhotoLink
from .purging import purge_context, purge_item


@login_required
def purge(request, kind, pk):
    models={'document':(Document,{'owner':request.user}),
            'entry':(Entry,{'project__owner':request.user}),
            'photo':(PhotoLink,{'entry__project__owner':request.user})}
    if kind not in models:
        raise Http404
    model,filters=models[kind]
    item=get_object_or_404(model,pk=pk,**filters)
    if not item.deleted_at:
        return HttpResponseBadRequest('Zuerst in den Papierkorb verschieben.')
    error=''
    if request.method=='POST':
        try:
            if request.POST.get('confirmed')!='1':
                raise ValidationError('Endgültiges Löschen ausdrücklich bestätigen.')
            warnings=purge_item(request.user,item,int(request.POST.get('version','0')),
                remove_costs=request.POST.get('remove_costs')=='1',
                cost_version=int(request.POST.get('cost_version','0')),cost_fingerprint=request.POST.get('cost_fingerprint'))
            messages.success(request,'Endgültig aus Projektakte entfernt. Derselbe Beleg kann erneut hochgeladen werden.' if kind=='document' else 'Endgültig aus dem Papierkorb entfernt.')
            for warning in warnings:messages.warning(request,warning)
            return redirect('trash')
        except (ValidationError,ValueError,ProtectedError) as exc:
            error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Verknüpfte Daten oder Löschangaben zuerst prüfen. Es wurde nichts gelöscht.'
    elif request.method!='GET':
        return HttpResponseBadRequest('Ungültige Methode.')
    context=purge_context(item)
    if context['source'] and context['source'].owner_id!=request.user.pk:
        raise Http404
    context.update(kind=kind,error=error)
    return render(request,'purge_confirm.html',context,status=400 if error else 200)
