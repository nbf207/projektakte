import io
from PIL import Image,ImageDraw

def synthetic_photo(number):
    image=Image.new('RGB',(1200,850),'#e6eeeb');d=ImageDraw.Draw(image)
    d.rectangle((0,0,1200,80),fill='#0b625e')
    d.text((40,30),f'PROJEKTAKTE | SYNTHETISCHES TESTBILD {number:02}',fill='white')
    # A deliberate diagram, not a photograph or real vehicle document.
    d.rounded_rectangle((190,210,1010,650),40,fill='#233b40',outline='#79918a',width=5)
    d.rounded_rectangle((240,260,960,510),20,fill='#b3c4bb')
    for x in (350,850):
        d.ellipse((x-75,320,x+75,470),fill='#1c3035',outline='#80b2a0',width=10)
        d.ellipse((x-30,365,x+30,425),fill='#5c8b7d')
    d.line((350,530,600,590,850,530),fill='#d5a44b',width=8)
    d.text((40,750),f'Schritt {(number-1)%5+1} - {'VORHER' if number==1 else 'NACHHER' if number==12 else 'EINBAU'}',fill='#172d34')
    out=io.BytesIO();image.save(out,'JPEG',quality=84);return out.getvalue()

def synthetic_pdf(text):
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import simpleSplit
    out=io.BytesIO();c=canvas.Canvas(out,pagesize=(595,842));c.setTitle('Synthetischer Projektakte-Testbeleg')
    y=790
    for line in text.splitlines():
        for sub in simpleSplit(line,'Helvetica',10,500):
            if y<50: c.showPage();y=790
            c.setFont('Helvetica',10);c.drawString(45,y,sub);y-=17
    c.save();return out.getvalue()
