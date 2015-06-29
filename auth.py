#!/usr/bin/env python

import redis
import random
import hashlib
import settings


class SessionManager(object):
    REDIS_SEG = 'auth'
    CHALLENGE_TIMEOUT = 90
    SESSION_TIMEOUT = 3600
    GOD_USER = 10
    TRUSTED_USER = 5
    BASIC_USER = 1
    KEY_TEMPLATE = '{prefix}:{class_seg}:{child_type}:{username}'

    r = redis.Redis()

    def __init__(self, username):
        self.username = username

    def key(self, child_type):
        return self.KEY_TEMPLATE.format(
            prefix=settings.redis_prefix,
            class_seg=self.REDIS_SEG,
            child_type=child_type,
            username=self.username,
        )

    @property
    def password_key(self):
        return self.key('password')

    @property
    def session_key(self):
        return self.key('session')

    @property
    def challenge_key(self):
        return self.key('challenge')

    @property
    def user_level_key(self):
        return self.key('user_level')

    @property
    def password(self):
        return self.r.get(self.password_key)

    @password.setter
    def password(self, value):
        return self.r.set(self.password_key, value)

    @property
    def ouput_limit(self):
        out = self.UNTRUSTED_LIMIT
        ul = self.user_level
        if ul > 0:
            out = self.UNTRUSTED_LIMIT * 2
        if ul >= 5:
            out = 12000
        return out

    def challenge(self):
        challenge = self.r.get(self.challenge_key)
        if challenge:
            return challenge
        challenge = hashlib.md5(str(random.random())).hexdigest()
        if not self.password:
            answer = None
        else:
            answer = hashlib.md5('{}{}\n'.format(challenge, self.password)).hexdigest()
        self.r.setex(self.challenge_key, answer, self.CHALLENGE_TIMEOUT)
        return challenge

    def challenge_ttl(self):
        ttl = self.r.ttl(self.challenge_key)
        if not ttl:
            return 0
        return int(ttl)

    def attempt(self, guess):
        answer = self.r.get(self.challenge_key)
        if answer == guess:
            return self.create_session()
        return 0

    def create_session(self):
        self.r.setex(self.session_key, 1, self.SESSION_TIMEOUT)
        return self.SESSION_TIMEOUT

    def has_session(self):
        ttl = self.r.ttl(self.session_key)
        if not ttl or ttl == -2:
            return 0
        return int(ttl)

    def destroy_session(self):
        return bool(self.r.delete(self.session_key))

    @property
    def user_level(self):
        return int(self.r.get(self.user_level_key))

    @user_level.setter
    def user_level(self, value):
        self.r.set(self.user_level_key, value)


def requires_login(user_level=SessionManager.TRUSTED_USER):
    def decorator(func):
        def wrapper(*args, **kwargs):
            if args[0].session.has_session() and args[0].session.user_level >= user_level:
                return func(*args, **kwargs)
            return 'Requires login, and user_level {}'.format(user_level)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator

if __name__ == '__main__':
    import sys
    username = sys.argv[1]
    sm = SessionManager(username)
    if len(sys.argv) == 3:
        sm.user_level = sys.argv[2]
    if len(sys.argv) == 4:
        sm.password = sys.argv[3]
    sm.create_session()
