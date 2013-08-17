import termcolor
import time

def log(strings, colors):
    out = '[{}] '.format(termcolor.colored(time.strftime('%Y-%m-%d %H:%M:%S'), 'cyan'))
    colors = list(colors)
    while len(colors) % 2:
        colors.append(None)
    for s, c in zip(strings, colors):
        if c:
            out += termcolor.colored(s, c[0], **c[1])
        else:
            out += s
    print(out)
