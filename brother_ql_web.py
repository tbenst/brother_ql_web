#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
This is a web service to print labels on Brother QL label printers.
"""

import sys, logging, random, json, argparse
from io import BytesIO

from bottle import run, route, get, post, response, request, jinja2_view as view, static_file, redirect
from PIL import Image, ImageDraw, ImageFont

from brother_ql.devicedependent import models, label_type_specs, label_sizes
from brother_ql.devicedependent import ENDLESS_LABEL, DIE_CUT_LABEL, ROUND_DIE_CUT_LABEL
from brother_ql import BrotherQLRaster, create_label
from brother_ql.backends import backend_factory, guess_backend

from font_helpers import get_fonts

logger = logging.getLogger(__name__)

LABEL_SIZES = [ (name, label_type_specs[name]['name']) for name in label_sizes]

try:
    with open('config.json', encoding='utf-8') as fh:
        CONFIG = json.load(fh)
except FileNotFoundError as e:
    with open('config.example.json', encoding='utf-8') as fh:
        CONFIG = json.load(fh)


@route('/')
def index():
    redirect('/labeldesigner')

@route('/static/<filename:path>')
def serve_static(filename):
    return static_file(filename, root='./static')

@route('/labeldesigner')
@view('labeldesigner.jinja2')
def labeldesigner():
    font_family_names = sorted(list(FONTS.keys()))
    return {'font_family_names': font_family_names,
            'fonts': FONTS,
            'label_sizes': LABEL_SIZES,
            'website': CONFIG['WEBSITE'],
            'label': CONFIG['LABEL']}

def get_label_context(request):
    """ might raise LookupError() """

    d = request.params.decode() # UTF-8 decoded form data

    font_family = d.get('font_family').rpartition('(')[0].strip()
    font_style  = d.get('font_family').rpartition('(')[2].rstrip(')')
    context = {
      'text':          d.get('text', None),
      'font_size': int(d.get('font_size', 100)),
      'font_family':   font_family,
      'font_style':    font_style,
      'label_size':    d.get('label_size', "62"),
      'kind':          label_type_specs[d.get('label_size', "62")]['kind'],
      'margin':    int(d.get('margin', 10)),
      'threshold': int(d.get('threshold', 70)),
      'align':         d.get('align', 'center'),
      'orientation':   d.get('orientation', 'standard'),
      'margin_top':    float(d.get('margin_top',    24))/100.,
      'margin_bottom': float(d.get('margin_bottom', 45))/100.,
      'margin_left':   float(d.get('margin_left',   35))/100.,
      'margin_right':  float(d.get('margin_right',  35))/100.,
    }
    context['margin_top']    = int(context['font_size']*context['margin_top'])
    context['margin_bottom'] = int(context['font_size']*context['margin_bottom'])
    context['margin_left']   = int(context['font_size']*context['margin_left'])
    context['margin_right']  = int(context['font_size']*context['margin_right'])

    context['fill_color']  = (255, 0, 0) if 'red' in context['label_size'] else (0, 0, 0)

    def get_font_path(font_family_name, font_style_name):
        try:
            if font_family_name is None or font_style_name is None:
                font_family_name = CONFIG['LABEL']['DEFAULT_FONTS']['family']
                font_style_name =  CONFIG['LABEL']['DEFAULT_FONTS']['style']
            font_path = FONTS[font_family_name][font_style_name]
        except KeyError:
            raise LookupError("Couln't find the font & style")
        return font_path

    context['font_path'] = get_font_path(context['font_family'], context['font_style'])

    def get_label_dimensions(label_size):
        try:
            ls = label_type_specs[context['label_size']]
        except KeyError:
            raise LookupError("Unknown label_size")
        return ls['dots_printable']

    width, height = get_label_dimensions(context['label_size'])
    if height > width: width, height = height, width
    if context['orientation'] == 'rotated': height, width = width, height
    context['width'], context['height'] = width, height

    return context

def create_label_im(text, **kwargs):
    label_type = kwargs['kind']
    im_font = ImageFont.truetype(kwargs['font_path'], kwargs['font_size'])
    im = Image.new('L', (20, 20), 'white')
    draw = ImageDraw.Draw(im)
    # workaround for a bug in multiline_textsize()
    # when there are empty lines in the text:
    lines = []
    for line in text.split('\n'):
        if line == '': line = ' '
        lines.append(line)
    text = '\n'.join(lines)
    linesize = im_font.getsize(text)
    textsize = draw.multiline_textsize(text, font=im_font)
    width, height = kwargs['width'], kwargs['height']
    if kwargs['orientation'] == 'standard':
        if label_type in (ENDLESS_LABEL,):
            height = textsize[1] + kwargs['margin_top'] + kwargs['margin_bottom']
    elif kwargs['orientation'] == 'rotated':
        if label_type in (ENDLESS_LABEL,):
            width = textsize[0] + kwargs['margin_left'] + kwargs['margin_right']
    im = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(im)
    if kwargs['orientation'] == 'standard':
        if label_type in (DIE_CUT_LABEL, ROUND_DIE_CUT_LABEL):
            vertical_offset  = (height - textsize[1])//2
            vertical_offset += (kwargs['margin_top'] - kwargs['margin_bottom'])//2
        else:
            vertical_offset = kwargs['margin_top']
        horizontal_offset = max((width - textsize[0])//2, 0)
    elif kwargs['orientation'] == 'rotated':
        vertical_offset  = (height - textsize[1])//2
        vertical_offset += (kwargs['margin_top'] - kwargs['margin_bottom'])//2
        if label_type in (DIE_CUT_LABEL, ROUND_DIE_CUT_LABEL):
            horizontal_offset = max((width - textsize[0])//2, 0)
        else:
            horizontal_offset = kwargs['margin_left']
    offset = horizontal_offset, vertical_offset
    draw.multiline_text(offset, text, kwargs['fill_color'], font=im_font, align=kwargs['align'])
    return im

@get('/api/preview/text')
@post('/api/preview/text')
def get_preview_image():
    context = get_label_context(request)
    im = create_label_im(**context)
    return_format = request.query.get('return_format', 'png')
    if return_format == 'base64':
        import base64
        response.set_header('Content-type', 'text/plain')
        return base64.b64encode(image_to_png_bytes(im))
    else:
        response.set_header('Content-type', 'image/png')
        return image_to_png_bytes(im)

def image_to_png_bytes(im):
    image_buffer = BytesIO()
    im.save(image_buffer, format="PNG")
    image_buffer.seek(0)
    return image_buffer.read()

@post('/api/print/image')
@get('/api/print/image')
def print_image():
    """
    API to print an image

    returns: JSON

    Name      Printable px   Description
    12         106           12mm endless
    29         306           29mm endless
    38         413           38mm endless
    50         554           50mm endless
    54         590           54mm endless
    62         696           62mm endless
    102       1164           102mm endless
    17x54      165 x  566    17mm x 54mm die-cut
    17x87      165 x  956    17mm x 87mm die-cut
    23x23      202 x  202    23mm x 23mm die-cut
    29x42      306 x  425    29mm x 42mm die-cut
    29x90      306 x  991    29mm x 90mm die-cut
    39x90      413 x  991    38mm x 90mm die-cut
    39x48      425 x  495    39mm x 48mm die-cut
    52x29      578 x  271    52mm x 29mm die-cut
    62x29      696 x  271    62mm x 29mm die-cut
    62x100     696 x 1109    62mm x 100mm die-cut
    102x51    1164 x  526    102mm x 51mm die-cut
    102x152   1164 x 1660    102mm x 153mm die-cut
    d12         94 x   94    12mm round die-cut
    d24        236 x  236    24mm round die-cut
    d58        618 x  618    58mm round die-cut
    """

    return_dict = {'success': False}

    try:
        params = request.params.decode()
        name = params.get('name', '62x29')
        image = request.files.get('image')
        red = params.get('red', False)
    except LookupError as e:
        return_dict['error'] = e.msg
        return return_dict

    if image is None:
        return_dict['error'] = 'Please provide the image for the label'
        return return_dict

    qlr = BrotherQLRaster(CONFIG['PRINTER']['MODEL'])
    create_label(qlr, image, name, red=red, threshold=context['threshold'],
                 cut=True)

    if not DEBUG:
        try:
            be = BACKEND_CLASS(CONFIG['PRINTER']['PRINTER'])
            be.write(qlr.data)
            be.dispose()
            del be
        except Exception as e:
            return_dict['message'] = str(e)
            logger.warning('Exception happened: %s', e)
            return return_dict

    return_dict['success'] = True
    if DEBUG: return_dict['data'] = str(qlr.data)
    return return_dict

@post('/api/print/text')
@get('/api/print/text')
def print_text():
    """
    API to print a label

    returns: JSON

    Ideas for additional URL parameters:
    - alignment
    """

    return_dict = {'success': False}

    try:
        context = get_label_context(request)
    except LookupError as e:
        return_dict['error'] = e.msg
        return return_dict

    if context['text'] is None:
        return_dict['error'] = 'Please provide the text for the label'
        return return_dict

    im = create_label_im(**context)
    if DEBUG: im.save('sample-out.png')

    if context['kind'] == ENDLESS_LABEL:
        rotate = 0 if context['orientation'] == 'standard' else 90
    elif context['kind'] in (ROUND_DIE_CUT_LABEL, DIE_CUT_LABEL):
        rotate = 'auto'

    qlr = BrotherQLRaster(CONFIG['PRINTER']['MODEL'])
    red = False
    if 'red' in context['label_size']:
        red = True
    create_label(qlr, im, context['label_size'], red=red, threshold=70, cut=True, rotate=rotate)

    if not DEBUG:
        try:
            be = BACKEND_CLASS(CONFIG['PRINTER']['PRINTER'])
            be.write(qlr.data)
            be.dispose()
            del be
        except Exception as e:
            return_dict['message'] = str(e)
            logger.warning('Exception happened: %s', e)
            return return_dict

    return_dict['success'] = True
    if DEBUG: return_dict['data'] = str(qlr.data)
    return return_dict

def main():
    global DEBUG, FONTS, BACKEND_CLASS, CONFIG
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default=False)
    parser.add_argument('--loglevel', type=lambda x: getattr(logging, x.upper()), default=False)
    parser.add_argument('--font-folder', default=False, help='folder for additional .ttf/.otf fonts')
    parser.add_argument('--default-label-size', default=False, help='Label size inserted in your printer. Defaults to 62.')
    parser.add_argument('--default-orientation', default=False, choices=('standard', 'rotated'), help='Label orientation, defaults to "standard". To turn your text by 90°, state "rotated".')
    parser.add_argument('--model', default=False, choices=models, help='The model of your printer (default: QL-500)')
    parser.add_argument('printer',  nargs='?', default=False, help='String descriptor for the printer to use (like tcp://192.168.0.23:9100 or file:///dev/usb/lp0)')
    args = parser.parse_args()

    if args.printer:
        CONFIG['PRINTER']['PRINTER'] = args.printer

    if args.port:
        PORT = args.port
    else:
        PORT = CONFIG['SERVER']['PORT']

    if args.loglevel:
        LOGLEVEL = args.loglevel
    else:
        LOGLEVEL = CONFIG['SERVER']['LOGLEVEL']

    if LOGLEVEL == 'DEBUG':
        DEBUG = True
    else:
        DEBUG = False

    if args.model:
        CONFIG['PRINTER']['MODEL'] = args.model

    if args.default_label_size:
        CONFIG['LABEL']['DEFAULT_SIZE'] = args.default_label_size

    if args.default_orientation:
        CONFIG['LABEL']['DEFAULT_ORIENTATION'] = args.default_orientation

    if args.font_folder:
        ADDITIONAL_FONT_FOLDER = args.font_folder
    else:
        ADDITIONAL_FONT_FOLDER = CONFIG['SERVER']['ADDITIONAL_FONT_FOLDER']


    logging.basicConfig(level=LOGLEVEL)

    try:
        selected_backend = guess_backend(CONFIG['PRINTER']['PRINTER'])
    except ValueError:
        parser.error("Couln't guess the backend to use from the printer string descriptor")
    BACKEND_CLASS = backend_factory(selected_backend)['backend_class']

    if CONFIG['LABEL']['DEFAULT_SIZE'] not in label_sizes:
        parser.error("Invalid --default-label-size. Please choose on of the following:\n:" + " ".join(label_sizes))

    FONTS = get_fonts()
    if ADDITIONAL_FONT_FOLDER:
        FONTS.update(get_fonts(ADDITIONAL_FONT_FOLDER))

    if not FONTS:
        sys.stderr.write("Not a single font was found on your system. Please install some or use the \"--font-folder\" argument.\n")
        sys.exit(2)

    for font in CONFIG['LABEL']['DEFAULT_FONTS']:
        try:
            FONTS[font['family']][font['style']]
            CONFIG['LABEL']['DEFAULT_FONTS'] = font
            logger.debug("Selected the following default font: {}".format(font))
            break
        except: pass
    if CONFIG['LABEL']['DEFAULT_FONTS'] is None:
        sys.stderr.write('Could not find any of the default fonts. Choosing a random one.\n')
        family =  random.choice(list(FONTS.keys()))
        style =   random.choice(list(FONTS[family].keys()))
        CONFIG['LABEL']['DEFAULT_FONTS'] = {'family': family, 'style': style}
        sys.stderr.write('The default font is now set to: {family} ({style})\n'.format(**CONFIG['LABEL']['DEFAULT_FONTS']))

    run(host=CONFIG['SERVER']['HOST'], port=PORT, debug=DEBUG)

if __name__ == "__main__":
    main()
