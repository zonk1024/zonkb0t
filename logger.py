import os
import time
import atexit
import settings
import termcolor


HOME_DIR = os.getenv('HOME')
LOG_DIR = os.path.join(HOME_DIR, 'log')
LOG_FILE = getattr(
    settings,
    'log_file',
    os.path.join(LOG_DIR, '{}.log'.format(settings.redis_prefix)),
)
LOG_FILE_OBJ = None

def close_log_file():
    LOG_FILE_OBJ.close()

def log(strings, colors):
    t = time.strftime('%Y-%m-%d %H:%M:%S')
    out = '[{}] '.format(termcolor.colored(t, 'cyan'))
    out_simple = '[{}] '.format(t)
    colors = list(colors)
    while len(colors) % 2:
        colors.append(None)
    for s, c in zip(strings, colors):
        if c:
            out += termcolor.colored(s, c[0], **c[1])
            try:
                out_simple += s
            except TypeError:
                out_simple += repr(s)
        else:
            out += s
            out_simple += s
    print out
    if LOG_FILE_OBJ:
        LOG_FILE_OBJ.write('{}\n'.format(out_simple))
        LOG_FILE_OBJ.flush()

if getattr(settings, 'file_logging', False):
    if 'log' not in os.listdir(os.getenv('HOME')):
        os.makedirs(LOG_DIR)
    LOG_FILE_OBJ = open(LOG_FILE, 'a')
    atexit.register(close_log_file)
