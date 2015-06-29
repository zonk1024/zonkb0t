import auth
import time
import Queue
import redis
import shlex
import logger
import random
import datetime
import settings
import threading
import subprocess
import urlgrabber
import BeautifulSoup


class UsageTracker(object):
    SECOND = 1
    MINUTE = SECOND * 60
    HOUR = MINUTE * 60
    DAY = HOUR * 24
    WEEK = DAY * 7
    MONTH = DAY * 30
    YEAR = DAY * 365

    redis_seg = 'usage'
    r = redis.Redis()

    def __init__(self, username, window=WEEK):
        self.username = username
        self.window = window

    @property
    def key(self):
        return '{}:{}:{}'.format(settings.redis_prefix, self.redis_seg, self.username)

    def update(self, value):
        t = int(time.time())
        self.r.zadd(self.key, value, t)
        self.r.zremrangebyscore(self.key, -1, t - self.window)

    def sum_range(self, window):
        t = int(time.time())
        return sum([int(v) for v in self.r.zrangebyscore(self.key, t - window, t)])

    @property
    def usage(self):
        return 'Command output of {}: Minute:{}  Hour:{}  Day:{}  Week:{}  Month:{}'.format(
            self.username,
            self.sum_range(self.MINUTE),
            self.sum_range(self.HOUR),
            self.sum_range(self.DAY),
            self.sum_range(self.WEEK),
            self.sum_range(self.MONTH),
        )

    @classmethod
    def get_usage(cls, username):
        return cls(username).usage

class Throttler(object):
    delay = .5
    output_function = None
    queue = Queue.Queue()
    started = False
    thread = None
    CHUNK_SIZE = 120

    @classmethod
    def start(cls):
        if not cls.started:
            cls.started = True
            cls.thread = threading.Thread(target=cls.worker)
            cls.thread.start()

    @classmethod
    def worker(cls):
        while True:
            cls.output_function(cls.queue.get())
            time.sleep(cls.delay)

    @classmethod
    def enqueue(cls, text, user_session):
        #text = text[:user_session.output_limit]
        UsageTracker(user_session.username).update(len(text))
        for line in text.split('\n'):
            for chunk in cls.n_at_a_time(line, cls.CHUNK_SIZE):
                cls.queue.put(chunk)
        cls.start()

    @classmethod
    def flush(cls):
        t = threading.Thread(target=cls._flush)
        t.start()

    @classmethod
    def _flush(cls):
        while True:
            try:
                cls.queue.get(False)
            except Queue.Empty:
                return

    @staticmethod
    def n_at_a_time(iterable, n, to_type=None):
        if to_type is None:
            to_type = type(iterable)
        iterator = iter(iterable)
        while True:
            out = to_type()
            for _ in xrange(n):
                try:
                    if to_type is list:
                        out.append(iterator.next())
                    else:
                        out += iterator.next()
                except StopIteration:
                    break
            if len(out) == n:
                yield out
            elif out:
                yield out
                break
            else:
                break

class ReloadException(Exception):
    pass

class BotCommand(object):
    r = redis.Redis()
    cmd_prefix = '%'
    cmd_map = {
        'help'    : '_help',
        'list'    : '_list',
        'dice'    : '_dice',
        'url'     : '_url',
        'run'     : '_run',
        'echo'    : '_echo',
        'sudo'    : '_admin',
        'login'   : '_login',
        'reddit'  : '_reddit',
        'reload'  : '_reload',
        'restart' : '_restart',
        'test'    : '_test',
        'flush'   : '_flush',
        'usage'   : '_usage',
    }

    def __init__(self, username, text, callback):
        self.username = username
        self.session = auth.SessionManager(username)
        self.text = text
        Throttler.output_function = callback
        self.args = None
        if self.text and self.text[0] == self.cmd_prefix:
            logger.log(('-!- COMMAND FROM -!- ', ': ', username), (settings.cd['a'], None, settings.cd['n']))
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
                Throttler.enqueue(str(output), user_session=self.session)
            else:
                logger.log(('-!- COMMAND FAILED -!- ',), (settings.cd['a'],))
        else:
            logger.log(('-!- UNREGISTERED COMMAND -!- ', ': ', cmd_name), (settings.cd['e'], None, settings.cd['cm']))

    #### HELP
    def _help(self, args):
        if not args:
            return 'Usage: `{}help [command]`\nCommands: {}'.format(self.cmd_prefix, ' '.join(self.cmd_map.keys()))
        if args[0] in self.cmd_map:
            return getattr(self, self.cmd_map[args[0]]).__doc__.format(cmd_prefix=self.cmd_prefix)
        return 'Command not found'

    #### ADMIN
    @auth.requires_login(user_level=auth.SessionManager.GOD_USER)
    def _admin(self, args):
        """Usage: `{cmd_prefix}sudo <alias> <target_function>`"""
        if not args:
            return None
        if len(args) == 3 and args[0] == 'add':
            self.cmd_map[args[1]] = args[2]
        if len(args) == 2 and args[0] == 'remove':
            del(self.cmd_map[args[1]])
        if len(args) == 1 and args[0] == 'show':
            return '\n'.join(['{} = {}'.format(cmd, val) for cmd, val in self.cmd_map.iteritems()])

    #### LIST
    def _list(self, args):
        """Usage: `{cmd_prefix}list [add|show|random|del] list_name`"""
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
            did = self.r.lpush(self._list_key(name))
        return str(did)

    def _list_show(self, args):
        if not args:
            return None
        output = []
        for name in args:
            output.extend(self.r.lrange(self._list_key(name))[::-1])
        return str(output)

    def _list_random(self, args):
        if not args:
            return None
        output = []
        for name in args:
            choose_from = self.r.lrange(self._list_key(name))
            output.append(choose_from[random.randint(0, len(choose_from) - 1)])
        if len(output) == 1:
            return str(output[0])
        return str(output)

    def _list_del(self, args):
        if not args:
            return None
        output = []
        for name in args:
            output.append(self.r.delete(self._list_key(name)))
        if len(output) == 1:
            return str(output[0])
        return str(output)

    def _list_key(self, name):
        return '{}:{}:{}'.format(settings.redis_prefix, 'list', name)

    #### DICE
    def _dice(self, args):
        """Usage: `{cmd_prefix}dice *args` with args in the form of (number)[dD](sides) -- 1D6 is default"""
        if not args:
            args = ['1d6']
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
    @auth.requires_login(user_level=auth.SessionManager.GOD_USER)
    def _run(self, args):
        """Usage: `{cmd_prefix}run cmd`"""
        if not args:
            return None
        try:
            return subprocess.check_output(args)
        except subprocess.CalledProcessError as e:
            return 'Command exited with error.'

    #### URL
    def _url(self, args):
        """Usage: `{cmd_prefix}url *urls`"""
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
        """Usage: `{cmd_prefix}echo text`"""
        return self.text.lstrip(self.cmd_prefix).lstrip(' ').lstrip('echo').lstrip(' ')

    #### LOGIN
    def _login(self, args):
        """Usage: `{cmd_prefix}login [challenge_response]`"""
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

    #### REDIT
    def _reddit(self, args):
        """Usage: `{cmd_prefix}reddit [*subreddits]`"""
        output = []
        args = args if args else ['']
        for arg in args:
            if arg:
                site = 'http://www.reddit.com/r/{}'.format(arg)
                logger.log((site, ), (None, ))
            else:
                site = 'http://www.reddit.com/'
            bs = BeautifulSoup.BeautifulSOAP(urlgrabber.urlread(site, size=2097152*10))
            output.extend(bs.findAll('a', 'title'))
        return '\n'.join('{}: {} {}'.format(i + 1, o.string, o.get('href')) for i, o in enumerate(output[:5]))

    #### RELOAD
    @auth.requires_login(user_level=auth.SessionManager.GOD_USER)
    def _reload(self, args):
        """Usage: `{cmd_prefix}reload`"""
        reload(auth)
        reload(logger)
        reload(settings)
        raise ReloadException

    #### RESTART
    @auth.requires_login(user_level=auth.SessionManager.GOD_USER)
    def _restart(self, args):
        """Usage: `{cmd_prefix}restart`"""
        raise RestartException

    #### TEST
    def _test(self, args):
        """Usage: `{cmd_prefix}test`"""
        return 'working'

    #### FLUSH
    def _flush(self, args):
        """Usage: `{cmd_prefix}flush`"""
        Throttler.flush()

    #### USAGE
    def _usage(self, args):
        """Usage: `{cmd_prefix}usage [*username]`"""
        output = []
        if not args:
            args = [self.username]
        for arg in args:
            output.append(UsageTracker.get_usage(arg))
        return '\n'.join(output)
