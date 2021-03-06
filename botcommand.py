import auth
import json
import time
import Queue
import redis
import shlex
import logger
import psutil
import random
import settings
import threading
import subprocess
import urlgrabber
import collections
import BeautifulSoup


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


class UsageTracker(object):
    SECOND = 1
    MINUTE = SECOND * 60
    HOUR = MINUTE * 60
    DAY = HOUR * 24
    WEEK = DAY * 7
    MONTH = DAY * 30
    YEAR = DAY * 365
    REDIS_SEG = 'usage'
    WINDOW = MONTH

    r = redis.Redis()

    def __init__(self, username):
        self.username = username

    @property
    def key(self):
        return '{}:{}:{}'.format(settings.redis_prefix, self.REDIS_SEG, self.username)

    def update(self, value):
        t = int(time.time())
        self.r.zadd(self.key, value, t)
        self.r.zremrangebyscore(self.key, -1, t - self.WINDOW)

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
    DELAY = .5
    CHUNK_SIZE = 120

    threads = collections.defaultdict(dict)

    def __init__(self, username, groupname, output_function):
        self.username = username
        self.groupname = groupname
        self.output_function = output_function

    @property
    def key(self):
        return self.groupname if self.groupname else self.username

    def start(self):
        if not self.threads[self.key].get('started'):
            self.threads[self.key]['started'] = True
            thread = threading.Thread(target=self.worker)
            thread.daemon = True
            thread.start()
            self.threads[self.key]['thread'] = thread

    def worker(self):
        while True:
            try:
                output_function, chunk = self.threads[self.key]['queue'].get(block=False)
                output_function(chunk)
                time.sleep(self.DELAY)
            except Queue.Empty:
                self.threads[self.key]['started'] = False
                return

    def enqueue(self, text):
        # text = text[:user_session.output_limit]
        UsageTracker(self.username).update(len(text))
        if 'queue' not in self.threads[self.key]:
            self.threads[self.key]['queue'] = Queue.Queue()
        for line in text.split('\n'):
            for chunk in n_at_a_time(line, self.CHUNK_SIZE):
                self.threads[self.key]['queue'].put((self.output_function, chunk))
        self.start()

    def flush(self):
        t = threading.Thread(target=self._flush)
        t.daemon = True
        t.start()

    def _flush(self):
        while True:
            try:
                self.threads[self.key]['queue'].get(block=False)
            except (Queue.Empty, KeyError):
                return


class ReloadException(Exception):
    pass


class BotCommand(object):
    CMD_PREFIX = '%'

    cmd_map = {
        'alias'       : '_admin',
        'client'      : '_client',
        'dice'        : '_dice',
        'echo'        : '_echo',
        'flush'       : '_flush',
        'help'        : '_help',
        'join'        : '_join',
        'leave'       : '_leave',
        'list'        : '_list',
        'login'       : '_login',
        'mysql'       : '_mysql',
        'ps'          : '_ps',
        'reddit'      : '_reddit',
        'reload'      : '_reload',
        'run'         : '_run',
        'status'      : '_status',
        'stfu'        : '_flush',
        'test'        : '_test',
        'url'         : '_url',
        'usage'       : '_usage',
        'weather'     : '_weather',
        'weather_raw' : '_weather_raw',
    }
    r = redis.Redis()

    def __init__(self, calling_class, username, text, groupname=None):
        self.username = username
        self.calling_class = calling_class
        self.groupname = groupname
        self.session = auth.SessionManager(username)
        self.text = text
        self.args = None
        self.throttler = Throttler(username, groupname, calling_class.sendText)
        if self.text and self.text[0] == self.CMD_PREFIX:
            logger.log(
                ('-!- COMMAND FROM -!- ', ': ', username),
                (settings.cd['a'], None, settings.cd['n']),
            )
            try:
                self.args = self.parse(text)
            except ValueError:
                logger.log(
                    ('-!- ERROR PARSING COMMAND -!- ', ': ', text),
                    (settings.cd['e'], None, settings.cd['e']),
                )
                self.args = []

    def parse(self, text):
        args = shlex.split(text)
        if not args or (len(args) > 1 and not args[0].startswith(self.CMD_PREFIX)):
            return []
        return args

    def execute(self):
        t = threading.Thread(target=self._execute)
        t.daemon = True
        t.start()

    def _execute(self):
        if not self.args:
            return None
        if len(self.args[0]) == len(self.CMD_PREFIX):
            # so they can do % cmd or %cmd
            self.args.pop(0)
        else:
            # trim
            self.args[0] = self.args[0].lstrip(self.CMD_PREFIX)
        cmd_name = self.args.pop(0)
        if cmd_name in self.cmd_map and hasattr(self, self.cmd_map[cmd_name]):
            output = getattr(self, self.cmd_map[cmd_name])(self.args)
            if output is not None:
                logger.log(
                    ('-!- COMMAND OUTPUT -!- ', ': ', output),
                    (settings.cd['a'], None, settings.cd['cm']),
                )
                self.throttler.enqueue(str(output))
            else:
                logger.log(
                    ('-!- COMMAND FAILED -!- ',),
                    (settings.cd['a'],),
                )
        else:
            logger.log(
                ('-!- UNREGISTERED COMMAND -!- ', ': ', cmd_name),
                (settings.cd['e'], None, settings.cd['cm']),
            )

    #### HELP
    def _help(self, args):
        if not args:
            return 'Usage: `{}help [command]`\nCommands: {}'.format(
                self.CMD_PREFIX,
                ' '.join(sorted(self.cmd_map.keys(), key=lambda x: x.lower())),
            )
        if args[0] in self.cmd_map:
            return getattr(self, self.cmd_map[args[0]]).__doc__.format(cmd_prefix=self.CMD_PREFIX)
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
            did = self.r.lpush(self._list_key(name), arg)
        return str(did)

    def _list_show(self, args):
        if not args:
            return None
        output = []
        for name in args:
            output.extend(self.r.lrange(self._list_key(name), 0, -1)[::-1])
        return str(output)

    def _list_random(self, args):
        if not args:
            return None
        output = []
        for name in args:
            choose_from = self.r.lrange(self._list_key(name), 0, -1)
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
        except subprocess.CalledProcessError:
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
        return self.text.lstrip(self.CMD_PREFIX).lstrip(' ').lstrip('echo').lstrip(' ')

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
        return 'Return md5({}<PASSWORD>\n) to log in.'.format(self.session.challenge())

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

    #### TEST
    def _test(self, args):
        """Usage: `{cmd_prefix}test`"""
        return 'working'

    #### FLUSH
    def _flush(self, args):
        """Usage: `{cmd_prefix}flush`"""
        self.throttler.flush()

    #### USAGE
    def _usage(self, args):
        """Usage: `{cmd_prefix}usage [*username]`"""
        output = []
        if not args:
            args = [self.username]
        for arg in args:
            output.append(UsageTracker.get_usage(arg))
        return '\n'.join(output)

    #### WEATHER
    def _weather(self, args, raw=False):
        """Usage: `{cmd_prefix}weather *zip_codes`"""
        if not args:
            args = ['92618']
        output = []
        for city in args:
            output.append(self._weather_get(city, raw=raw))
        return '\n'.join(output)

    def _weather_get(self, city, raw=False):
        url = 'http://api.openweathermap.org/data/2.5/weather?{}={}'
        if all(c in '0123456789' for c in city):
            try:
                resp = urlgrabber.urlread(url.format('zip', city), size=2097152*10)
            except urlgrabber.grabber.URLGrabError:
                resp = 'Failed to fetch weather for {}'.format(repr(city))
        else:
            try:
                resp = urlgrabber.urlread(url.format('q', self._weather_parse_city(city)), size=2097152*10)
            except urlgrabber.grabber.URLGrabError:
                resp = 'Failed to fetch weather for {}'.format(repr(city))
        if raw:
            return resp
        try:
            json_data = json.loads(resp)
            return 'Current weather for {city}: {desc}, low:{low:.1f} high:{cur:.1f} currently:{high:.1f}'.format(
                city=json_data['name'],
                desc=json_data['weather'][0]['description'],
                low=self._weather_convert(json_data['main']['temp_min']),
                cur=self._weather_convert(json_data['main']['temp']),
                high=self._weather_convert(json_data['main']['temp_max']),
            )
        except (KeyError, ValueError):
            return 'API error for {}: {}'.format(repr(city), resp)

    def _weather_parse_city(self, value):
        city = value
        state = ''
        country = 'us'
        if city.count(',') == 2:
            city, state, country = value.split(',')
            country = '{}'.format(country)
        elif city.count(',') == 1:
            city, state = value.split(',')
        return ','.join([v for v in (city.strip(), state.strip(), country.strip()) if v])

    def _weather_convert(self, value):
        return (value - 273.15) * 1.8 + 32

    def _weather_raw(self, args):
        """Usage: `{cmd_prefix}weather_raw *zip_codes`"""
        return self._weather(args, raw=True)

    #### JOIN
    @auth.requires_login(user_level=auth.SessionManager.TRUSTED_USER)
    def _join(self, args):
        for arg in args:
            chan = '{}{}'.format('#' if not arg.startswith('#') else '', arg)
            print chan
            for account in settings.accounts:
                account.client.joinGroup(chan)

    #### LEAVE
    @auth.requires_login(user_level=auth.SessionManager.TRUSTED_USER)
    def _leave(self, args):
        args = args if args else [self.groupname]
        if not args:
            args = [] if not self.groupname else [self.groupname]
        for arg in args:
            chan = '{}{}'.format('#' if not arg.startswith('#') else '', arg)
            for account in settings.accounts:
                account.client.leave(chan)

    #### STATUS
    @auth.requires_login(user_level=auth.SessionManager.BASIC_USER)
    def _status(self, args):
        output = []
        for arg in args:
            for account in settings.accounts:
                person = account.client.getPerson(arg)
                if person:
                    # print dir(person)
                    output.append('Idle:{}  Commands:{}  Status:{}  IMP_W:{}  Online:{}'.format(
                        repr(person.getIdleTime()),
                        repr(person.getPersonCommands()),
                        repr(person.getStatus()),
                        repr(person.imperson_whois()),
                        repr(person.isOnline()),
                    ))
                else:
                    output.append('Person {} not found.'.format(repr(self.username)))
                    # import ipdb; ipdb.set_trace() # BREAKPOINT
        return '\n'.join(output)

    #### CLIENT
    @auth.requires_login(user_level=auth.SessionManager.GOD_USER)
    def _client(self, args):
        if not args:
            return None
        for account in settings.accounts:
            try:
                return repr(getattr(account, args[0])(*args[1:]))
            except TypeError:
                return repr(getattr(account, args[0]))
            except AttributeError:
                return repr(dir(account))

    #### MYSQL
    @auth.requires_login(user_level=auth.SessionManager.GOD_USER)
    def _mysql(self, args):
        """Usage: `{cmd_prefix}mysql DB Query [DB Query, ...]`"""
        output = []
        for arg1, arg2 in n_at_a_time(args, 2):
            cmd = '/usr/bin/mysql {} -e "{}"'.format(arg1, arg2)
            try:
                output.append(subprocess.check_output(cmd))
            except Exception as e:
                output.append('Failed on command: {} with error {}'.format(repr(cmd), e))
        return '\n'.join(output)

    #### PS
    @auth.requires_login(user_level=auth.SessionManager.BASIC_USER)
    def _ps(self, args):
        return '\n'.join([str(p) for p in psutil.process_iter()])
