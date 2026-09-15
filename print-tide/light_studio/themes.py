"""Colour themes for the printer ropes.

A theme is a complete palette: every colour any state, ripple, identify flash
or hue sweep can produce comes from here, so switching the theme recolours the
whole wall consistently and nothing is left over from a previous look. The
*shapes* of the animations (water rising through bands, rain, breathing,
heartbeat, dashes) never change with the theme; only the colours do.

Pure data, no imports, so both the layout validator and the renderer can use
it without a cycle.

Palette keys
------------
water        printed portion of a running print
rest         unprinted remainder right above the waterline (band 0)
deep         unprinted remainder at the far end (last band)
drop         the falling raindrop, and the tail it leaves at speed
splash       the landing flash inside the water
prep_low/high  breathing body while preparing;  prep  the rising sweep
pause / pause_mark      breathing pause body and its steady end marks
error / error_mark      breathing error body and its steady end marks
collect      ready to collect, held until the door opens
stopped      stopped early (cancelled or dismissed failure), bed not cleared
offline      heartbeat while telemetry is missing
unknown      dashes for an unrecognised state
idle         the resting colour
ident_body / ident_core  the identify flash
ripple_complete / ripple_other  cross-wall ripple tints
hue_low / hue_high / hue_soften  the "hue sweep" used by the accent's
             rainbow mode and the completion wash: swept back and forth
             between the two hues (a full circle if they span >= 1), then
             mixed hue_soften of the way towards ``water``.
"""

DEFAULT_THEME = 'pink'

PALETTE_KEYS = (
    'water', 'rest', 'deep', 'drop', 'splash',
    'prep_low', 'prep_high', 'prep',
    'pause', 'pause_mark', 'error', 'error_mark',
    'collect', 'stopped', 'offline', 'unknown', 'idle',
    'ident_body', 'ident_core', 'ripple_complete', 'ripple_other',
    'hue_low', 'hue_high', 'hue_soften',
)

THEMES = {
    'pink': {
        'label': 'Pink',
        'blurb': 'Every colour a shade of pink, no white. States are told apart '
                 'by lightness and motion: hot-pink water, deeper bands above it, '
                 'the lightest pink for collect, deep fuchsia breathing on error.',
        'palette': {
            'water': (255, 95, 175), 'rest': (235, 55, 150), 'deep': (90, 25, 60),
            'drop': (255, 150, 205), 'splash': (255, 135, 200),
            'prep_low': (48, 14, 34), 'prep_high': (104, 30, 72), 'prep': (255, 150, 205),
            'pause': (255, 125, 195), 'pause_mark': (230, 30, 130),
            'error': (255, 15, 110), 'error_mark': (255, 95, 175),
            'collect': (255, 150, 205), 'stopped': (165, 45, 105),
            'offline': (150, 70, 125), 'unknown': (170, 95, 140), 'idle': (190, 70, 130),
            'ident_body': (255, 95, 175), 'ident_core': (255, 150, 205),
            'ripple_complete': (255, 150, 205), 'ripple_other': (200, 40, 130),
            'hue_low': 0.84, 'hue_high': 0.93, 'hue_soften': 0.30,
        },
    },
    'classic': {
        'label': 'Classic',
        'blurb': 'One hue per meaning: orange water over deep blue, green to '
                 'collect, yellow pause, red error, magenta stopped, cyan idle, '
                 'and a full rainbow on completion.',
        'palette': {
            'water': (255, 110, 0), 'rest': (0, 56, 200), 'deep': (0, 20, 96),
            'drop': (255, 200, 120), 'splash': (255, 235, 190),
            'prep_low': (0, 14, 30), 'prep_high': (0, 46, 66), 'prep': (0, 232, 255),
            'pause': (255, 210, 0), 'pause_mark': (255, 240, 170),
            'error': (255, 0, 0), 'error_mark': (255, 235, 220),
            'collect': (0, 198, 84), 'stopped': (255, 0, 120),
            'offline': (120, 100, 215), 'unknown': (150, 150, 160), 'idle': (0, 220, 240),
            'ident_body': (0, 210, 235), 'ident_core': (255, 255, 255),
            'ripple_complete': (40, 235, 155), 'ripple_other': (60, 155, 255),
            'hue_low': 0.0, 'hue_high': 1.0, 'hue_soften': 0.0,
        },
    },
    'ocean': {
        'label': 'Ocean',
        'blurb': 'Blues, teals and aqua. Bright cyan water over deep sea, pale '
                 'aqua to collect, pure blue breathing on error, indigo when stopped.',
        'palette': {
            'water': (0, 200, 255), 'rest': (0, 90, 210), 'deep': (0, 18, 60),
            'drop': (170, 240, 255), 'splash': (200, 250, 255),
            'prep_low': (0, 12, 34), 'prep_high': (0, 35, 80), 'prep': (120, 220, 255),
            'pause': (40, 160, 255), 'pause_mark': (200, 235, 255),
            'error': (0, 40, 255), 'error_mark': (220, 240, 255),
            'collect': (150, 255, 240), 'stopped': (60, 0, 200),
            'offline': (40, 60, 120), 'unknown': (110, 140, 150), 'idle': (0, 120, 140),
            'ident_body': (0, 210, 235), 'ident_core': (240, 255, 255),
            'ripple_complete': (200, 255, 250), 'ripple_other': (0, 120, 255),
            'hue_low': 0.45, 'hue_high': 0.70, 'hue_soften': 0.20,
        },
    },
    'ember': {
        'label': 'Ember',
        'blurb': 'Fire tones. Amber water over glowing red coals, pale gold to '
                 'collect, yellow pause, pure red error, dark crimson when stopped.',
        'palette': {
            'water': (255, 140, 0), 'rest': (200, 40, 0), 'deep': (60, 10, 0),
            'drop': (255, 230, 120), 'splash': (255, 245, 200),
            'prep_low': (34, 8, 0), 'prep_high': (90, 24, 0), 'prep': (255, 200, 80),
            'pause': (255, 220, 60), 'pause_mark': (255, 250, 200),
            'error': (255, 0, 0), 'error_mark': (255, 240, 220),
            'collect': (255, 240, 150), 'stopped': (140, 0, 30),
            'offline': (110, 50, 20), 'unknown': (150, 110, 90), 'idle': (200, 80, 0),
            'ident_body': (255, 140, 0), 'ident_core': (255, 255, 240),
            'ripple_complete': (255, 240, 180), 'ripple_other': (255, 80, 0),
            'hue_low': 0.0, 'hue_high': 0.12, 'hue_soften': 0.20,
        },
    },
    'forest': {
        'label': 'Forest',
        'blurb': 'Greens. Bright leaf-green water over deep moss, pale mint to '
                 'collect, lime pause, orange-red error, olive when stopped.',
        'palette': {
            'water': (60, 220, 80), 'rest': (0, 120, 60), 'deep': (0, 30, 15),
            'drop': (200, 255, 200), 'splash': (230, 255, 230),
            'prep_low': (0, 26, 18), 'prep_high': (0, 45, 20), 'prep': (150, 255, 150),
            'pause': (230, 255, 60), 'pause_mark': (250, 255, 200),
            'error': (255, 40, 0), 'error_mark': (255, 230, 200),
            'collect': (200, 255, 180), 'stopped': (120, 90, 0),
            'offline': (40, 90, 60), 'unknown': (120, 150, 120), 'idle': (0, 150, 90),
            'ident_body': (60, 220, 80), 'ident_core': (245, 255, 245),
            'ripple_complete': (220, 255, 220), 'ripple_other': (0, 200, 120),
            'hue_low': 0.22, 'hue_high': 0.45, 'hue_soften': 0.20,
        },
    },
}

# Curated combinations: progress, remaining, collect, available, alarm, category.
# Supporting tones are derived consistently; the five original palettes above
# remain byte-for-byte unchanged.
CURATED = [
 ('aurora','Aurora','Electric mint crossing violet dusk.','#54FFD2','#682AAA','#D5FF93','#287D92','#FF4979','Nature'),
 ('sunset','Sunset','Tangerine progress against plum, with a rose finish.','#FF8D37','#6B275D','#FFD3A5','#BF507C','#FF3048','Warm'),
 ('lavender','Lavender','Lilac, ink blue and a soft peach finish.','#B999FF','#182B68','#FFD3C5','#746BA0','#F4568F','Soft'),
 ('cherry','Cherry blossom','Rose petals, burgundy shadows and spring green.','#FFA2BA','#722448','#D8FFC9','#B9667D','#EF174A','Soft'),
 ('glacier','Glacier','Ice blue, navy and crisp cool light.','#83D9FF','#123B79','#D9FFF9','#267BA0','#FF566A','Nature'),
 ('desert','Desert','Ochre, terracotta and sage after the heat.','#EBC065','#783F2A','#CBE1A0','#9B7650','#EC4C36','Warm'),
 ('copper','Copper','Burnished orange against petrol green.','#F89462','#234B52','#FFDDB0','#728C85','#FA394B','Warm'),
 ('moonlight','Moonlight','Silver-blue progress, indigo shadows, pale gold finish.','#C4D5EC','#303A67','#F9E6AF','#566687','#F283A4','Minimal'),
 ('candy','Candy','Bubblegum, grape and a lemon-sherbet finish.','#FF75CD','#6331A5','#FFF095','#67C8E8','#FF304C','Neon'),
 ('cyberpunk','Cyberpunk','Acid yellow and electric violet, hot coral alarms.','#EAFE32','#8620D6','#7AFFBC','#187F99','#FF355D','Neon'),
 ('synthwave','Synthwave','Hot magenta over cobalt with turquoise highlights.','#FC4DAC','#243ED0','#78FFE3','#784CAC','#FF533D','Neon'),
 ('acid','Acid','Chartreuse progress over ultraviolet shadows.','#B8FF2C','#522593','#DFFFA6','#308F72','#FF4A37','Neon'),
 ('lagoon','Lagoon','Turquoise water, blue depths and sunlit peach.','#38D7B2','#246A9E','#FFD29C','#3B9387','#F95B67','Nature'),
 ('rose_gold','Rose gold','Blush metal, wine shadows and warm ivory.','#F8B29A','#723D54','#FFE3B6','#B87877','#E84E6B','Soft'),
 ('monochrome','Monochrome','Neutral silver, charcoal and white; motion carries meaning.','#B8BBC4','#413427','#F0F0F0','#737373','#DADADA','Minimal'),
]

def _rgb(value):
    return tuple(int(value[i:i+2],16) for i in (1,3,5))

def _blend(a,b,k):
    return tuple(round(x*(1-k)+y*k) for x,y in zip(a,b))

for name,label,blurb,water,rest,collect,idle,error,category in CURATED:
    w,r,c,i,e=map(_rgb,(water,rest,collect,idle,error))
    THEMES[name]={'label':label,'blurb':blurb,'category':category,'palette':{
      'water':w,'rest':r,'deep':_blend(r,(0,0,0),.55),
      'drop':_blend(w,c,.7),'splash':_blend(w,c,.85),
      'prep_low':_blend(i,(0,0,0),.84),'prep_high':_blend(i,(0,0,0),.5),'prep':_blend(i,c,.5),
      'pause':_blend(w,(255,195,55),.65),'pause_mark':_blend(c,(255,225,140),.5),
      'error':e,'error_mark':_blend(e,c,.7),'collect':c,'stopped':_blend(e,r,.6),
      'offline':_blend(i,r,.65),'unknown':_blend(r,c,.3),'idle':i,
      'ident_body':w,'ident_core':c,'ripple_complete':c,'ripple_other':w,
      'hue_low':0.,'hue_high':1.,'hue_soften':.65}}

for name,cat in [('pink','Soft'),('classic','Minimal'),('ocean','Nature'),('ember','Warm'),('forest','Nature')]:
    THEMES[name]['category']=cat
ORDER = tuple(THEMES)
COLOUR_KEYS = tuple(k for k in PALETTE_KEYS if not k.startswith('hue_'))


def names():
    return list(ORDER)


def palette(name=None, overrides=None):
    """Resolve a palette without mutating a built-in. Callers validate overrides."""
    theme = THEMES.get(name if isinstance(name,str) else None) or THEMES[DEFAULT_THEME]
    base=theme['palette']
    if not overrides:
        return base
    out=dict(base)
    for key, value in overrides.items():
        if key in COLOUR_KEYS:
            out[key]=tuple(value)
    # Highlights follow custom primary colours, unless explicitly overridden.
    derived={}
    if 'water' in overrides:
        derived.update(drop=_blend(out['water'],out['collect'],.65),splash=_blend(out['water'],out['collect'],.8),ident_body=out['water'],ripple_other=out['water'])
    if 'rest' in overrides: derived['deep']=_blend(out['rest'],(0,0,0),.78)
    if 'prep' in overrides: derived.update(prep_low=_blend(out['prep'],(0,0,0),.85),prep_high=_blend(out['prep'],(0,0,0),.6))
    for key in ('pause','error'):
        if key in overrides: derived[key+'_mark']=_blend(out[key],(255,255,255),.65)
    if 'collect' in overrides: derived.update(ripple_complete=out['collect'],ident_core=out['collect'])
    out.update({k:v for k,v in derived.items() if k not in overrides})
    return out


def describe():
    return [{'name':name,'label':THEMES[name]['label'],'blurb':THEMES[name]['blurb'],
             'category':THEMES[name]['category'],
             'summary': {'pink':'Pink throughout; lightness and motion distinguish each state.','classic':'Orange progress, blue remainder, green finish and red alerts.','ocean':'Cyan progress over deep blue with an aqua finish.','ember':'Amber progress, red coals and a pale gold finish.','forest':'Leaf green and moss, mint finish and orange-red alerts.'}.get(name,THEMES[name]['blurb']),
             'palette':{k:list(v) if isinstance(v,tuple) else v for k,v in palette(name).items()},
             'swatches':{k:list(palette(name)[k]) for k in COLOUR_KEYS}} for name in ORDER]
