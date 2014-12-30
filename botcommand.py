import subprocess
import urlgrabber
import BeautifulSoup
import redis
import shlex
import random
import settings
import logger
import auth


class ReloadException(Exception):
    pass

class BotCommand(object):
    r = redis.Redis()
    cmd_prefix = '%'
    cmd_map = {
        'list'   : '_list',
        'dice'   : '_dice',
        'url'    : '_url',
        'run'    : '_run',
        'echo'   : '_echo',
        'sudo'   : '_admin',
        'login'  : '_login',
        'reddit' : '_reddit',
        'reload' : '_reload',
        'test'   : '_test',
    }

    def __init__(self, sender, text, callback):
        self.sender = sender
        self.session = auth.SessionManager(sender)
        self.text = text
        self.callback = callback
        self.args = None
        if self.text and self.text[0] == self.cmd_prefix:
            logger.log(('-!- COMMAND FROM -!- ', ': ', sender), (settings.cd['a'], None, settings.cd['n']))
            try:
                self.args = self.parse(text)
            except ValueError as e:
                logger.log(('-!- ERROR PARSING COMMAND -!- ', ': ', text), (settings.cd['e'], None, settings.cd['e']))
                self.args = []

    def parse(self, text):
        args = shlex.split(text)
        if not args or (len(args) > 1 and not args[0].startswith(self.cmd_prefix)):
            return []
        return args

    def run(self):
        if not self.args:
            return None
        if len(self.args[0]) == len(self.cmd_prefix):
            # so they can do ! cmd or !cmd
            self.args.pop(0)
        else:
            # trim
            self.args[0] = self.args[0].lstrip(self.cmd_prefix)
        cmd_name = self.args.pop(0)
        if cmd_name in self.cmd_map and hasattr(self, self.cmd_map[cmd_name]):
            output = getattr(self, self.cmd_map[cmd_name])(self.args)
            if output is not None:
                logger.log(('-!- COMMAND OUTPUT -!- ', ': ', output), (settings.cd['a'], None, settings.cd['cm']))
                self.callback(str(output))
            else:
                logger.log(('-!- COMMAND FAILED -!- ',), (settings.cd['a'],))
        else:
            logger.log(('-!- UNREGISTERED COMMAND -!- ', ': ', cmd_name), (settings.cd['e'], None, settings.cd['cm']))

    #### ADMIN
    def _admin(self, args):
        if not args:
            return None
        if not self.session.has_session() or self.session.user_level() < 5:
            return None
        if len(args) == 3 and args[0] == 'add':
            self.cmd_map[args[1]] = args[2]
        if len(args) == 2 and args[0] == 'remove':
            del(self.cmd_map[args[1]])
        if len(args) == 1 and args[0] == 'show':
            return '\n'.join(['{} = {}'.format(cmd, val) for cmd, val in self.cmd_map.iteritems()])

    #### LIST
    def _list(self, args):
        output = ""
        list_map = {
            'add'    : '_list_add',
            'show'   : '_list_show',
            'random' : '_list_random',
            'del'    : '_list_del',
        }

        if not args:
            return None

        list_cmd = args.pop(0)
        if list_cmd in list_map and hasattr(self, list_map[list_cmd]):
            output = getattr(self, list_map[list_cmd])(args)
        return output

    def _list_add(self, args):
        if not args:
            return None
        name = args.pop(0)
        did = False
        for arg in args:
            did = self.r.lpush('{}{}{}'.format(settings.redis_prefix, 'list:', name), arg)
        return str(did)

    def _list_show(self, args):
        if not args:
            return None
        output = []
        for name in args:
            output.extend(self.r.lrange('{}{}{}'.format(settings.redis_prefix, 'list:', name), 0, -1)[::-1])
        return str(output)

    def _list_random(self, args):
        if not args:
            return None
        output = []
        for name in args:
            choose_from = self.r.lrange('{}{}{}'.format(settings.redis_prefix, 'list:', name), 0, -1)
            output.append(choose_from[random.randint(0, len(choose_from) - 1)])
        if len(output) == 1:
            return str(output[0])
        return str(output)

    def _list_del(self, args):
        if not args:
            return None
        output = []
        for name in args:
            output.append(self.r.delete('{}{}{}'.format(settings.redis_prefix, 'list:', name)))
        if len(output) == 1:
            return str(output[0])
        return str(output)

    #### DICE
    def _dice(self, args):
        if not args:
            return None
        output = []
        for roll in args:
            roll = roll.lower().replace(' ', '')
            roll_output = {
                'group' : roll,
                'rolls' : [],
                'sum'   : 0,
            }
            groups = [i for i in roll.split('+')]
            for group in groups:
                group_rolls = self._dice_roll(group)
                roll_output['rolls'].append(group_rolls)
                roll_output['sum'] = sum(sum(i) for i in roll_output['rolls'])
            output.append(roll_output)
        if len(output) == 1:
            return 'group {group} had sum {sum} with rolls {rolls}'.format(**output[0])
        return '\n'.join('group {group} had sum {sum} with rolls {rolls}'.format(**i) for i in output)

    def _dice_roll(self, roll):
        roll_parts = roll.split('d')
        if not roll_parts:
            return None
        if len(roll_parts) == 1:
            return [int(roll_parts[0])]
        if len(roll_parts) == 2 and not roll_parts[0]:
            roll_parts[0] = 1
        if len(roll_parts) == 2 and not roll_parts[1]:
            roll_parts[1] = 6
        return [random.randint(1, int(roll_parts[1])) for i in range(int(roll_parts[0]))]

    #### RUN
    def _run(self, args):
        if not args:
            return None
        if self.session.has_session() and self.session.user_level() >= 10:
            try:
                return subprocess.check_output(args)
            except subprocess.CalledProcessError as e:
                return 'Command exited with error.'

    #### URL
    def _url(self, args):
        if not args:
            return None
        output = []
        for url in args:
            if not any(url.startswith(i) for i in ('https://', 'http://')):
                url = 'http://{}'.format(url)
            bs = BeautifulSoup.BeautifulSoup(urlgrabber.urlread(url, size=2097152*10))
            output.append(bs.title.string)
        return '\n'.join(output)

    #### ECHO
    def _echo(self, args):
        return self.text.lstrip(self.cmd_prefix).lstrip(' ').lstrip('echo').lstrip(' ')

    #### LOGIN
    def _login(self, args):
        ttl = self.session.has_session()
        c_ttl = self.session.challenge_ttl()
        if ttl:
            if args and args[0].lower() == 'exit':
                return self._login_exit()
            else:
                return self._login_status()
        elif c_ttl and args:
            return self._login_attempt(args[0])
        else:
            return self._login_challenge()

    def _login_exit(self):
        if self.session.destroy_session():
            return 'Session destroyed.'

    def _login_status(self):
        ttl = self.session.has_session()
        if ttl:
            return 'Currently logged in. Session ttl is {} seconds.'.format(ttl)
        else:
            return 'You are not currently logged in.'

    def _login_challenge(self):
        return 'Return md5({}<PASSWORD>) to log in.'.format(self.session.challenge())

    def _login_attempt(self, attempt):
        ttl = self.session.attempt(attempt)
        if ttl:
            return 'Logged in. Session ttl is {} seconds.'.format(ttl)
        else:
            return 'Attempt failed. Challenge ttl is {} seconds.'.format(self.session.challenge_ttl())

    def _reddit(self, args):
        output = []
        if not args:
            args = ['']
        for arg in args:
            if arg:
                site = 'http://www.reddit.com/r/{}'.format(arg)
                logger.log((site, ), (None, ))
            else:
                site = 'http://www.reddit.com/'
            bs = BeautifulSoup.BeautifulSOAP(urlgrabber.urlread(site, size=2097152*10))
            output.extend(bs.findAll('a', 'title'))
        return '\n'.join('{}: {} {}'.format(i + 1, o.string, o.get('href')) for i, o in enumerate(output[:5]))

    def _reload(self, args):
        reload(auth)
        reload(logger)
        reload(settings)
        raise ReloadException

    def _test(self, args):
        return 'yup'
