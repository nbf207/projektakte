"""Bounded resumable uploads and serial CPU-limited local video encoding."""
import json
import math
import re
import os
import shutil
import subprocess
import time
from pathlib import Path
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse, FileResponse, StreamingHttpResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe
from .models import Entry, KnowledgeVideo
from .operations import operation_gate
from .private_files import private_path

MAX_SOURCE=1024*1024*1024
MAX_RESULT=200*1024*1024
CHUNK=4*1024*1024

def path_for(video,suffix):
    root=settings.MEDIA_ROOT/'knowledge'
    root.mkdir(parents=True,exist_ok=True)
    return root/(video.pk.hex+suffix)

def command(tool):
    root=settings.DATA_DIR/'tools/ffmpeg'
    loader=root/'ld-linux-aarch64.so.1'
    if not loader.is_file() or not (root/tool).is_file():
        raise ValidationError('Video-Encoder ist nicht eingerichtet.')
    return [str(loader),'--library-path',str(root),str(root/tool)]

@login_required
@require_POST
def start(request,entry_id):
    entry=get_object_or_404(Entry,pk=entry_id,project__owner=request.user,deleted_at=None)
    try:
        size=int(request.POST.get('size','0'))
        if not 0<size<=MAX_SOURCE:raise ValueError()
        with operation_gate(),transaction.atomic():
            if KnowledgeVideo.objects.filter(owner=request.user,state__in=['uploading','queued','processing']).count()>=3:
                return JsonResponse({'error':'Bitte laufende Video-Uploads oder Verarbeitung abwarten.'},status=409)
            if shutil.disk_usage(settings.MEDIA_ROOT).free<size*2+1024**3:
                return JsonResponse({'error':'Nicht genug freier Speicher für Video und Umwandlung.'},status=413)
            video=KnowledgeVideo.objects.create(owner=request.user,entry=entry,size=size,
                name=Path(request.POST.get('name','Video').replace(chr(92),'/')).name[:200])
        return JsonResponse({'id':str(video.pk),'offset':0})
    except (ValueError,TypeError):
        return JsonResponse({'error':'Originalvideos dürfen höchstens 1 GB groß sein.'},status=413)

@login_required
@require_POST
def chunk(request,pk):
    video=get_object_or_404(KnowledgeVideo,pk=pk,owner=request.user)
    upload=request.FILES.get('chunk')
    try:
        offset=int(request.POST.get('offset','-1'))
        if not upload or not 0<upload.size<=CHUNK:raise ValueError()
    except (ValueError,TypeError):
        return JsonResponse({'error':'Ungültiges Upload-Teilstück.'},status=400)
    with operation_gate(),transaction.atomic():
        video=KnowledgeVideo.objects.get(pk=video.pk,owner=request.user)
        if video.state!='uploading' or offset!=video.offset:
            return JsonResponse({'offset':video.offset,'state':video.state},status=409)
        if offset+upload.size>video.size:
            return JsonResponse({'error':'Dateigröße überschritten.'},status=413)
        path=path_for(video,'.source')
        with path.open('r+b' if path.exists() else 'w+b') as out:
            out.truncate(offset);out.seek(offset)
            for part in upload.chunks():out.write(part)
            out.flush();os.fsync(out.fileno())
        video.offset+=upload.size
        if video.offset==video.size:video.state='queued'
        video.updated=timezone.now();video.save(update_fields=['offset','state','updated'])
    return JsonResponse({'offset':video.offset,'state':video.state})

@login_required
@require_safe
def status(request,pk):
    video=get_object_or_404(KnowledgeVideo,pk=pk,owner=request.user)
    return JsonResponse({'offset':video.offset,'size':video.size,'state':video.state,'error':video.error})

@login_required
@require_POST
def cancel(request,pk):
    with operation_gate(),transaction.atomic():
        video=get_object_or_404(KnowledgeVideo,pk=pk,owner=request.user)
        if video.state=='processing':
            return JsonResponse({'error':'Video wird gerade verarbeitet. Bitte Abschluss abwarten.'},status=409)
        if video.state=='ready':
            return JsonResponse({'error':'Fertiges Video bleibt am Eintrag erhalten.'},status=409)
        for suffix in ('.source','.mp4.tmp'):path_for(video,suffix).unlink(missing_ok=True)
        video.delete()
    return JsonResponse({'cancelled':True})

@login_required
@require_safe
def download(request,pk):
    video=get_object_or_404(KnowledgeVideo,pk=pk,owner=request.user,state='ready',entry__deleted_at=None)
    path=private_path('media','knowledge/'+video.pk.hex+'.mp4')
    size=path.stat().st_size
    requested=request.headers.get('Range','')
    if requested:
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
        if not match or not any(match.groups()):return HttpResponse(status=416,headers={'Content-Range':f'bytes */{size}'})
        first,last=match.groups()
        start=int(first) if first else max(0,size-int(last))
        end=min(int(last),size-1) if first and last else size-1
        if start>end or start>=size:return HttpResponse(status=416,headers={'Content-Range':f'bytes */{size}'})
        def chunks():
            with path.open('rb') as stream:
                stream.seek(start);remaining=end-start+1
                while remaining:
                    part=stream.read(min(65536,remaining))
                    if not part:break
                    remaining-=len(part);yield part
        response=StreamingHttpResponse(chunks() if request.method!='HEAD' else [],status=206,content_type='video/mp4')
        response['Content-Range']=f'bytes {start}-{end}/{size}'
        response['Content-Length']=str(end-start+1)
    else:
        response=FileResponse(path.open('rb'),content_type='video/mp4')
    response['Accept-Ranges']='bytes'
    response['Cache-Control']='private, no-store'
    response['Content-Disposition']='inline; filename="video.mp4"'
    response['X-Content-Type-Options']='nosniff'
    return response

def process_video():
    if (settings.DATA_DIR/'.maintenance').exists():return False
    # Only the existing worker calls this, under its process-wide lock.
    with operation_gate(),transaction.atomic():
        video=KnowledgeVideo.objects.filter(state__in=['queued','processing']).order_by('created').first()
        if not video:return False
        video.state='processing';video.error='';video.updated=timezone.now();video.save()
    output=path_for(video,'.mp4.tmp')
    try:
        with path_for(video,'.source').open('rb') as stream:header=stream.read(16)
        if not (header[4:8]==b'ftyp' or header[:4]==bytes.fromhex('1a45dfa3') or (header[:4]==b'RIFF' and header[8:12]==b'AVI ')):
            raise ValidationError('Bitte MP4, MOV, WebM oder AVI verwenden.')
        raw=subprocess.check_output(command('ffprobe')+['-v','error','-protocol_whitelist','file,pipe','-show_entries','format=duration:stream=width,height,codec_type','-of','json',str(path_for(video,'.source'))],
            timeout=30,stderr=subprocess.DEVNULL)
        metadata=json.loads(raw)
        for stream in metadata.get('streams',[]):
            if stream.get('codec_type')=='video' and int(stream.get('width',0))*int(stream.get('height',0))>34000000:
                raise ValidationError('Videoauflösung ist zu groß für diesen Server.')
        duration=float(metadata['format']['duration'])
        if not math.isfinite(duration) or duration<=0:raise ValueError()
        # Total target includes audio and mux overhead; reduce bitrate for long clips.
        bitrate=min(4000000,int(MAX_RESULT*.94*8/duration))
        if bitrate<24000:raise ValidationError('Video ist für 200 MB zu umfangreich. Bitte in Abschnitte teilen.')
        audio=min(96000,bitrate//5)
        video_rate=bitrate-audio
        for attempt in range(2):
            args=command('ffmpeg')+['-nostdin','-y','-v','error','-max_alloc','67108864','-protocol_whitelist','file,pipe','-threads','1',
                '-i',str(path_for(video,'.source')),'-map','0:v:0','-map','0:a:0?','-sn','-dn',
                '-vf',"scale='trunc(min(1920,iw)/2)*2':-2"]
            args=args+['-c:v','libx264','-preset','veryfast','-threads','1','-filter_threads','1',
                '-b:v',str(video_rate),'-maxrate',str(video_rate),'-bufsize',str(video_rate*2),
                '-pix_fmt','yuv420p','-c:a','aac','-b:a',str(audio),'-movflags','+faststart','-f','mp4',str(output)]
            with subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) as proc:
                try:
                    deadline=time.monotonic()+6*3600
                    while proc.poll() is None:
                        (settings.DATA_DIR/'worker.heartbeat').touch()
                        if time.monotonic()>deadline or (output.exists() and output.stat().st_size>MAX_RESULT*1.1):
                            proc.kill();proc.wait();raise ValidationError('Umwandlung abgebrochen; Original bleibt erhalten. Video bitte in Abschnitte teilen.')
                        time.sleep(2)
                    if proc.returncode:raise ValidationError('Video konnte nicht umgewandelt werden. Original bleibt erhalten.')
                finally:
                    if proc.poll() is None:proc.kill();proc.wait()
            if 0<output.stat().st_size<=MAX_RESULT:break
            video_rate=int(video_rate*.85);output.unlink(missing_ok=True)
        if not output.exists() or output.stat().st_size>MAX_RESULT:raise ValidationError('200-MB-Ziel nicht erreicht. Original bleibt erhalten.')
        with operation_gate(),transaction.atomic():
            os.replace(output,path_for(video,'.mp4'))
            video.state='ready';video.duration=duration;video.updated=timezone.now();video.save()
        with operation_gate():path_for(video,'.source').unlink(missing_ok=True)
    except (OSError,ValueError,KeyError,subprocess.SubprocessError,ValidationError):
        with operation_gate():
            KnowledgeVideo.objects.filter(pk=video.pk).update(state='failed',error='Umwandlung nicht abgeschlossen. Original bleibt gespeichert; bitte Feedback melden.',updated=timezone.now())
    finally:output.unlink(missing_ok=True)
    return True
