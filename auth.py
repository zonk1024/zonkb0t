#!/usr/bin/env python

import redis
import logger
import random
import hashlib
import settings

class SessionManager(object):
    r = redis.Redis()
    redis_seg = 'auth:'
    challenge_timeout = 90
    session_timeout = 3600

    def __init__(self, user):
        self.user = user
        template_key = '{a}{b}{}:{c}'
        template_dict = {'a': settings.redis_prefix, 'b': self.redis_seg, 'c': self.user}
        #botname:auth:password:username
        self.password_key = template_key.format('password', **template_dict)
        #botname:auth:session:username
        self.session_key = template_key.format('session', **template_dict)
        #botname:auth:challenge:username
        self.challenge_key = template_key.format('challenge', **template_dict)
        #botname:auth:user_level:username
        self.user_level_key = template_key.format('user_level', **template_dict)

    @property
    def password(self):
        return self.r.get(self.password_key)

    @password.setter
    def password(self, value):
        return self.r.set(self.password_key, value)

    def challenge(self):
        challenge = self.r.get(self.challenge_key)
        if challenge:
            return challenge
        challenge = hashlib.md5(str(random.random())).hexdigest()
        if not self.password:
            answer = None
        else:
            answer = hashlib.md5('{}{}\n'.format(challenge, self.password)).hexdigest()
        self.r.setex(self.challenge_key, answer, self.challenge_timeout)
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
        self.r.setex(self.session_key, 1, self.session_timeout)
        return self.session_timeout

    def has_session(self):
        ttl = self.r.ttl(self.session_key)
        if not ttl or ttl == -2:
            return 0
        return int(ttl)

    def destroy_session(self):
        return bool(self.r.delete(self.session_key))

    def user_level(self):
        return int(self.r.get(self.user_level_key))


if __name__ == '__main__':
    import sys
    username = sys.argv[1]
    sm = SessionManager(username)
    if len(sys.argv) == 3:
        sm.password = sys.argv[2]
    sm.create_session()
