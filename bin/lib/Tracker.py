#!/usr/bin/env python3
# -*-coding:UTF-8 -*
import json
import os
import re
import sys
import time
import uuid
import yara
import datetime
import base64

from ail_typo_squatting import runAll
import math

from collections import defaultdict
from flask import escape
from textblob import TextBlob
from nltk.tokenize import RegexpTokenizer

sys.path.append(os.environ['AIL_BIN'])
##################################
# Import Project packages
##################################
from packages import Date
from lib.ail_core import get_objects_tracked, get_object_all_subtypes
from lib import ConfigLoader
from lib import item_basic
from lib import Tag
from lib.Users import User

config_loader = ConfigLoader.ConfigLoader()
r_cache = config_loader.get_redis_conn("Redis_Cache")

r_tracker = config_loader.get_db_conn("Kvrocks_Trackers")

r_serv_tracker = config_loader.get_db_conn("Kvrocks_Trackers") # TODO REMOVE ME

items_dir = config_loader.get_config_str("Directories", "pastes")
if items_dir[-1] == '/':
    items_dir = items_dir[:-1]
config_loader = None

email_regex = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,6}'
email_regex = re.compile(email_regex)

special_characters = set('[<>~!?@#$%^&*|()_-+={}":;,.\'\n\r\t]/\\')
special_characters.add('\\s')

# NLTK tokenizer
tokenizer = RegexpTokenizer('[\&\~\:\;\,\.\(\)\{\}\|\[\]\\\\/\-/\=\'\"\%\$\?\@\+\#\_\^\<\>\!\*\n\r\t\s]+',
                                    gaps=True, discard_empty=True)

###############
#### UTILS ####
def is_valid_uuid_v4(UUID):
    if not UUID:
        return False
    UUID = UUID.replace('-', '')
    try:
        uuid_test = uuid.UUID(hex=UUID, version=4)
        return uuid_test.hex == UUID
    except:
        return False

def is_valid_regex(tracker_regex):
    try:
        re.compile(tracker_regex)
        return True
    except:
        return False

def is_valid_mail(email):
    result = email_regex.match(email)
    if result:
        return True
    else:
        return False

def verify_mail_list(mail_list):
    for mail in mail_list:
        if not is_valid_mail(mail):
            return {'status': 'error', 'reason': 'Invalid email', 'value': mail}, 400
    return None

##-- UTILS --##
###############

################################################################################################
################################################################################################
################################################################################################

class Tracker:
    def __init__(self, tracker_uuid):
        self.uuid = tracker_uuid

    def get_uuid(self):
        return self.uuid

    def exists(self):
        return r_tracker.exists(f'tracker:{self.uuid}')

    def _set_field(self, field, value):
        r_tracker.hset(f'tracker:{self.uuid}', field, value)

    def get_date(self):
        return r_tracker.hget(f'tracker:{self.uuid}', 'date')

    def get_last_change(self, r_str=False):
        last_change = r_tracker.hget(f'tracker:{self.uuid}', 'last_change')
        if r_str and last_change:
            last_change = datetime.datetime.fromtimestamp(float(last_change)).strftime('%Y-%m-%d %H:%M:%S')
        return last_change

    def get_first_seen(self):
        return r_tracker.hget(f'tracker:{self.uuid}', 'first_seen')

    def get_last_seen(self):
        return r_tracker.hget(f'tracker:{self.uuid}', 'last_seen')

    def _set_first_seen(self, date):
        self._set_field('first_seen', date)

    def _set_last_seen(self, date):
        self._set_field('last_seen', date)

    def _exist_date(self, date):
        return r_serv_tracker.exists(f'tracker:objs:{self.uuid}:{date}')

    # TODO: ADD CACHE ???
    def update_daterange(self, date=None):
        first_seen = self.get_first_seen()
        # Added Object
        if date:
            date = int(date)
            first_seen = self.get_first_seen()
            # if op == 'add':
            if not first_seen:
                    self._set_first_seen(date)
                    self._set_last_seen(date)
            else:
                first_seen = int(first_seen)
                last_seen = int(self.get_last_seen())
                if date < first_seen:
                    self._set_first_seen(date)
                if date > last_seen:
                    self._set_last_seen(date)
        else:
            last_seen = self.get_last_seen()
            if first_seen and last_seen:
                valid_first_seen = self._exist_date(first_seen)
                valid_last_seen = self._exist_date(last_seen)
                # update first seen
                if not valid_first_seen:
                    for date in Date.get_daterange(first_seen, last_seen):
                        if self._exist_date(date):
                            self._set_first_seen(date)
                            valid_first_seen = True
                            break
                # update last seen
                if not valid_last_seen:
                    for date in reversed(Date.get_daterange(first_seen, last_seen)):
                        if self._exist_date(date):
                            self._set_first_seen(date)
                            valid_last_seen = True
                            break
                if not valid_first_seen or not valid_last_seen:
                    r_tracker.hdel(f'tracker:{self.uuid}', 'first_seen')
                    r_tracker.hdel(f'tracker:{self.uuid}', 'last_seen')

    def get_description(self):
        return r_tracker.hget(f'tracker:{self.uuid}', 'description')

    def get_level(self):
        level = r_tracker.hget(f'tracker:{self.uuid}', 'level')
        if not level:
            level = 0
        return int(level)

    def is_level_user(self):
        return self.get_level() == 0

    def is_level_global(self):
        return self.get_level() == 1

    def _set_level(self, level, tracker_type=None, user=None):
        if not tracker_type:
            tracker_type = self.get_type()
        if level == 0:  # user only
            if not user:
                user = self.get_user()
            r_serv_tracker.sadd(f'user:tracker:{user}', self.uuid)
            r_serv_tracker.sadd(f'user:tracker:{user}:{tracker_type}', self.uuid)
        elif level == 1:  # global
            r_serv_tracker.sadd('global:tracker', self.uuid)
            r_serv_tracker.sadd(f'global:tracker:{tracker_type}', self.uuid)
        self._set_field('level', level)

    def get_filters(self):
        filters = r_tracker.hget(f'tracker:{self.uuid}', 'filters')
        if not filters:
            return {}
        else:
            return json.loads(filters)

    def set_filters(self, filters):
        if filters:
            self._set_field('filters', json.dumps(filters))

    def get_tracked(self):
        return r_tracker.hget(f'tracker:{self.uuid}', 'tracked')

    def get_type(self):
        return r_tracker.hget(f'tracker:{self.uuid}', 'type')

    def get_tags(self):
        return r_tracker.smembers(f'tracker:tags:{self.uuid}')

    def _set_tags(self, tags):
        for tag in tags:
            tag = escape(tag)
            r_serv_tracker.sadd(f'tracker:tags:{self.uuid}', tag)
            Tag.create_custom_tag(tag) # TODO CUSTOM TAGS

    def mail_export(self):
        return r_tracker.exists(f'tracker:mail:{self.uuid}')

    def get_mails(self):
        return r_tracker.smembers(f'tracker:mail:{self.uuid}')

    def _set_mails(self, mails):
        for mail in mails:
            r_serv_tracker.sadd(f'tracker:mail:{self.uuid}', escape(mail))

    def get_user(self):
        return r_tracker.hget(f'tracker:{self.uuid}', 'user_id')

    def webhook_export(self):
        return r_tracker.hexists(f'tracker:mail:{self.uuid}', 'webhook')

    def get_webhook(self):
        return r_tracker.hget(f'tracker:{self.uuid}', 'webhook')

    def get_sparkline(self, nb_day=6):
        date_range_sparkline = Date.get_date_range(nb_day)
        sparkline = []
        for date in date_range_sparkline:
            nb_seen_this_day = self.get_nb_objs_by_date(date)
            if nb_seen_this_day is None:
                nb_seen_this_day = 0
            sparkline.append(int(nb_seen_this_day))
        return sparkline

    def get_rule(self):
        yar_path = self.get_tracked()
        return yara.compile(filepath=os.path.join(get_yara_rules_dir(), yar_path))

    # TODO get objects/ tracked items


    # TODO sparkline
    def get_meta(self, options):
        if not options:
            options = set()
        meta = {'uuid': self.uuid,
                'tracked': self.get_tracked(),  # TODO TO CHECK
                'type': self.get_type(),
                'date': self.get_date(),
                'first_seen': self.get_first_seen(),
                'last_seen': self.get_last_seen()}
        if 'user' in options:
            meta['user'] = self.get_user()
        if 'level' in options:
            meta['level'] = self.get_level()
        if 'description' in options:
            meta['description'] = self.get_description()
        if 'tags' in options:
            meta['tags'] = self.get_tags()
        if 'filters' in options:
            meta['filters'] = self.get_filters()
        if 'mails' in options:
            meta['mails'] = self.get_mails()
        if 'webhooks' in options:
            meta['webhook'] = self.get_webhook()
        if 'sparkline' in options:
            meta['sparkline'] = self.get_sparkline(6)
        return meta

    def _add_to_dashboard(self, obj_type, subtype, obj_id):
        mess = f'{self.uuid}:{int(time.time())}:{obj_type}:{subtype}:{obj_id}'
        if self.is_level_user():
            user = self.get_user()
            r_serv_tracker.lpush(f'trackers:user:{user}', mess)
            r_serv_tracker.ltrim(f'trackers:user:{user}', 0, 9)
        else:
            r_serv_tracker.lpush('trackers:dashboard', mess)
            r_serv_tracker.ltrim(f'trackers:dashboard', 0, 9)

    # - TODO Data Retention TO Implement - #
    # Or Daily/Monthly Global DB Cleanup:
    #    Iterate on each tracker:
    #       Iterate on each Obj:
    #           Iterate on each date:
    #               Delete from tracker range if date limit exceeded
    # - TODO
    def add(self, obj_type, subtype, obj_id, date=None):
        if not subtype:
            subtype = ''
        if not date:
            date = Date.get_today_date_str()

        new_obj_date = r_serv_tracker.sadd(f'tracker:objs:{self.uuid}:{date}', f'{obj_type}:{subtype}:{obj_id}')
        new_obj = r_serv_tracker.sadd(f'obj:trackers:{obj_type}:{subtype}:{obj_id}', self.uuid)
        # MATCHES
        if new_obj:
            r_serv_tracker.zincrby(f'tracker:match:{self.uuid}', 1, 'total')
            r_serv_tracker.zincrby(f'tracker:match:{self.uuid}', 1, obj_type)

        # Only save date for daterange objects - Needed for the DB Cleaner
        if obj_type != 'item':  # not obj_date:
            r_serv_tracker.sadd(f'obj:tracker:{obj_type}:{subtype}:{obj_id}:{self.uuid}', date)
            r_serv_tracker.sadd(f'tracker:objs:{self.uuid}:{obj_type}', f'{subtype}:{obj_id}')

        if new_obj_date:
            self.update_daterange(date)

        self._add_to_dashboard(obj_type, subtype, obj_id)

    def get_objs_by_type(self, obj_type):
        return r_serv_tracker.smembers(f'tracker:objs:{self.uuid}:{obj_type}')

    def get_nb_objs_by_date(self, date):
        return r_serv_tracker.scard(f'tracker:objs:{self.uuid}:{date}')

    def get_objs_by_date(self, date):
        return r_serv_tracker.smembers(f'tracker:objs:{self.uuid}:{date}')

    def get_objs_by_daterange(self, date_from, date_to):
        objs = set()
        for date in Date.get_daterange(date_from, date_to):
            objs |= self.get_objs_by_date(date)
        return objs

    def get_obj_dates(self, obj_type, subtype, obj_id):
        if obj_type == 'item':
            return [item_basic.get_item_date(obj_id)]
        else:
            return r_serv_tracker.smembers(f'obj:tracker:{obj_type}:{subtype}:{obj_id}:{self.uuid}')

    def remove(self, obj_type, subtype, obj_id):
        if not subtype:
            subtype = ''

        for date in self.get_obj_dates(obj_type, subtype, obj_id):
            r_serv_tracker.srem(f'tracker:objs:{self.uuid}:{date}', f'{obj_type}:{subtype}:{obj_id}')
            r_serv_tracker.srem(f'obj:tracker:{obj_type}:{subtype}:{obj_id}:{self.uuid}', date)

        r_serv_tracker.srem(f'obj:trackers:{obj_type}:{subtype}:{obj_id}', self.uuid)
        r_serv_tracker.srem(f'tracker:objs:{self.uuid}', f'{obj_type}:{subtype}:{obj_id}')
        # MATCHES
        r_serv_tracker.zincrby(f'tracker:match:{self.uuid}', -1, 'total')
        r_serv_tracker.zincrby(f'tracker:match:{self.uuid}', -1, obj_type)
        self.update_daterange()

    # TODO escape tags ????
    # TODO escape mails ????
    def create(self, tracker_type, to_track, user_id, level, description=None, filters={}, tags=[], mails=[], webhook=None):
        if self.exists():
            raise Exception('Error: Tracker already exists')

        # YARA
        if tracker_type == 'yara_custom' or tracker_type == 'yara_default':
            to_track = save_yara_rule(tracker_type, to_track, tracker_uuid=self.uuid)
            tracker_type = 'yara'

        elif tracker_type == 'typosquatting':
            domain = to_track.split(" ")[0]
            typo_generation = runAll(domain=domain, limit=math.inf, formatoutput="text", pathOutput="-", verbose=False) # TODO REPLACE LIMIT BY -1
            for typo in typo_generation:
                r_serv_tracker.sadd(f'tracker:typosquatting:{to_track}', typo)

        # create metadata
        self._set_field('tracked', to_track)
        self._set_field('type', tracker_type)
        self._set_field('date', datetime.date.today().strftime("%Y%m%d"))
        self._set_field('user_id', user_id)
        if description:
            self._set_field('description', escape(description))
        if webhook:
            self._set_field('webhook', webhook)

        # create all tracker set
        r_serv_tracker.sadd(f'all:tracker:{tracker_type}', to_track) # TODO RENAME ????
        # create tracker - uuid map
        r_serv_tracker.sadd(f'all:tracker_uuid:{tracker_type}:{to_track}', self.uuid)  # TODO RENAME ????
        r_serv_tracker.sadd('trackers:all', self.uuid)
        r_serv_tracker.sadd(f'trackers:all:{tracker_type}', self.uuid)

        # TRACKER LEVEL
        self._set_level(level, tracker_type=tracker_type, user=user_id)

        # create tracker tags list
        if tags:
            self._set_tags(tags)

        # create tracker mail notification list
        if mails:
            self._set_mails(mails)

        # TODO Delete filters
        # Filters
        if not filters:
            filters = {}
            for obj_type in get_objects_tracked():
                filters[obj_type] = {}
        else:
            self.set_filters(filters)
        for obj_type in filters:
            r_serv_tracker.sadd(f'trackers:objs:{tracker_type}:{obj_type}', to_track)
            r_serv_tracker.sadd(f'trackers:uuid:{tracker_type}:{to_track}', f'{self.uuid}:{obj_type}')

        self._set_field('last_change', time.time())

        # toggle refresh module tracker list/set
        trigger_trackers_refresh(tracker_type)
        return self.uuid

    def edit(self, tracker_type, to_track, level, description=None, filters={}, tags=[], mails=[], webhook=None):

        # edit tracker
        old_type = self.get_type()
        old_to_track = self.get_tracked()
        old_level = self.get_level()
        user_id = self.get_user()

        # YARA
        if tracker_type == 'yara_custom' or tracker_type == 'yara_default':
            # create yara rule
            if tracker_type == 'yara_default' and old_type == 'yara':
                if not is_default_yara_rule(old_to_track):
                    filepath = get_yara_rule_file_by_tracker_name(old_to_track)
                    if filepath:
                        os.remove(filepath)
            to_track = save_yara_rule(tracker_type, to_track, tracker_uuid=self.uuid)
            tracker_type = 'yara'

        # TODO TYPO EDIT
        elif tracker_type == 'typosquatting':
            pass

        if tracker_type != old_type:
            # LEVEL
            if old_level == 0:
                r_serv_tracker.srem(f'user:tracker:{user_id}:{old_type}', self.uuid)
            elif old_level == 1:
                r_serv_tracker.srem(f'global:tracker:{old_type}', self.uuid)
            self._set_level(level, tracker_type=tracker_type, user=user_id)
            # Delete OLD YARA Rule File
            if old_type == 'yara':
                if not is_default_yara_rule(old_to_track):
                    filepath = get_yara_rule_file_by_tracker_name(old_to_track)
                    if filepath:
                        os.remove(filepath)
            self._set_field('type', tracker_type)

            # create all tracker set
            r_serv_tracker.srem(f'all:tracker:{old_type}', old_to_track)
            r_serv_tracker.sadd(f'all:tracker:{tracker_type}', to_track)
            # create tracker - uuid map
            r_serv_tracker.srem(f'all:tracker_uuid:{old_type}:{old_to_track}', self.uuid)
            r_serv_tracker.sadd(f'all:tracker_uuid:{tracker_type}:{to_track}', self.uuid)
            # create all tracker set by type
            r_serv_tracker.srem(f'trackers:all:{old_type}', self.uuid)
            r_serv_tracker.sadd(f'trackers:all:{tracker_type}', self.uuid)

        # Same Type
        elif level != old_level:
            if level == 0:
                r_serv_tracker.srem('global:tracker', self.uuid)
            elif level == 1:
                r_serv_tracker.srem(f'user:tracker:{user_id}', self.uuid)
            self._set_level(level, tracker_type=tracker_type, user=user_id)

        # To Track Edited
        if to_track != old_to_track:
            self._set_field('tracked', to_track)

        self._set_field('description', description)
        self._set_field('webhook', webhook)

        # Tags
        nb_old_tags = r_serv_tracker.scard(f'tracker:tags:{self.uuid}')
        if nb_old_tags > 0 or tags:
            r_serv_tracker.delete(f'tracker:tags:{self.uuid}')
            self._set_tags(tags)

        # Mails
        nb_old_mails = r_serv_tracker.scard(f'tracker:mail:{self.uuid}')
        if nb_old_mails > 0 or mails:
            r_serv_tracker.delete(f'tracker:mail:{self.uuid}')
            self._set_mails(mails)

        nb_old_sources = r_serv_tracker.scard(f'tracker:sources:{self.uuid}') # TODO FILTERS
        if nb_old_sources > 0 or sources:
            r_serv_tracker.delete(f'tracker:sources:{self.uuid}')
            self._set_sources(sources)

        # Refresh Trackers
        trigger_trackers_refresh(tracker_type)
        if tracker_type != old_type:
            trigger_trackers_refresh(old_type)

        self._set_field('last_change', time.time())
        return self.uuid

    def delete(self):
        pass


def create_tracker(tracker_type, to_track, user_id, level, description=None, filters={}, tags=[], mails=[], webhook=None, tracker_uuid=None):
    if not tracker_uuid:
        tracker_uuid = str(uuid.uuid4())
    tracker = Tracker(tracker_uuid)
    return tracker.create(tracker_type, to_track, user_id, level, description=description, filters=filters, tags=tags,
                          mails=mails, webhook=webhook)

def _re_create_tracker(tracker_type, tracker_uuid, to_track, user_id, level, description=None, filters={}, tags=[], mails=[], webhook=None, first_seen=None, last_seen=None):
    create_tracker(tracker_type, to_track, user_id, level, description=description, filters=filters,
                   tags=tags, mails=mails, webhook=webhook, tracker_uuid=tracker_uuid)

def get_trackers_types():
    return ['word', 'set', 'regex', 'typosquatting', 'yara']

def get_trackers():
    return r_serv_tracker.smembers(f'trackers:all')

def get_trackers_by_type(tracker_type):
    return r_serv_tracker.smembers(f'trackers:all:{tracker_type}')

def _get_tracked_by_obj_type(tracker_type, obj_type):
    return r_serv_tracker.smembers(f'trackers:objs:{tracker_type}:{obj_type}')

def get_trackers_by_tracked_obj_type(tracker_type, obj_type, tracked):
    trackers_uuid = set()
    for res in r_serv_tracker.smembers(f'trackers:uuid:{tracker_type}:{tracked}'):
        tracker_uuid, tracker_obj_type = res.split(':', 1)
        if tracker_obj_type == obj_type:
            trackers_uuid.add(tracker_uuid)
    return trackers_uuid

def get_trackers_by_tracked(tracker_type, tracked):
    return r_serv_tracker.smembers(f'all:tracker_uuid:{tracker_type}:{tracked}')

def get_user_trackers_by_tracked(tracker_type, tracked, user_id):
    user_trackers = get_user_trackers(user_id, tracker_type=tracker_type)
    trackers_uuid = get_trackers_by_tracked(tracker_type, tracked)
    return trackers_uuid.intersection(user_trackers)

def get_trackers_tracked_by_type(tracker_type):
    return r_serv_tracker.smembers(f'all:tracker:{tracker_type}')

def get_global_trackers(tracker_type=None):
    if tracker_type:
        return r_serv_tracker.smembers(f'global:tracker:{tracker_type}')
    else:
        return r_serv_tracker.smembers('global:tracker')

def get_user_trackers(user_id, tracker_type=None):
    if tracker_type:
        return r_serv_tracker.smembers(f'user:tracker:{user_id}:{tracker_type}')
    else:
        return r_serv_tracker.smembers(f'user:tracker:{user_id}')

def get_nb_global_trackers(tracker_type=None):
    if tracker_type:
        return r_serv_tracker.scard(f'global:tracker:{tracker_type}')
    else:
        return r_serv_tracker.scard('global:tracker')

def get_nb_user_trackers(user_id, tracker_type=None):
    if tracker_type:
        return r_serv_tracker.scard(f'user:tracker:{user_id}:{tracker_type}')
    else:
        return r_serv_tracker.scard(f'user:tracker:{user_id}')

def get_user_trackers_meta(user_id, tracker_type=None):
    metas = []
    for tracker_uuid in get_user_trackers(user_id, tracker_type=tracker_type):
        tracker = Tracker(tracker_uuid)
        metas.append(tracker.get_meta(options={'mails', 'sparkline', 'tags'}))
    return metas

def get_global_trackers_meta(tracker_type=None):
    metas = []
    for tracker_uuid in get_global_trackers(tracker_type=tracker_type):
        tracker = Tracker(tracker_uuid)
        metas.append(tracker.get_meta(options={'mails', 'sparkline', 'tags'}))
    return metas

def get_trackers_graph_by_day(l_trackers, num_day=31, date_from=None, date_to=None):
    if date_from and date_to:
        date_range = Date.substract_date(date_from, date_to)
    else:
        date_range = Date.get_date_range(num_day)
    list_tracker_stats = []
    for tracker_uuid in l_trackers:
        dict_tracker_data = []
        tracker = Tracker(tracker_uuid)
        for date_day in date_range:
            nb_seen_this_day = tracker.get_nb_objs_by_date(date_day)
            if nb_seen_this_day is None:
                nb_seen_this_day = 0
            dict_tracker_data.append({"date": date_day, "value": int(nb_seen_this_day)})
        list_tracker_stats.append({"name": tracker.get_tracked(), "Data": dict_tracker_data})
    return list_tracker_stats

def get_trackers_dashboard():
    trackers = []
    for raw in r_serv_tracker.lrange('trackers:dashboard', 0, -1):
        tracker_uuid, timestamp, obj_type, subtype, obj_id = raw.split(':', 4)
        tracker = Tracker(tracker_uuid)
        meta = tracker.get_meta(options={'tags'})
        timestamp = datetime.datetime.fromtimestamp(float(timestamp)).strftime('%Y-%m-%d %H:%M:%S')
        meta['timestamp'] = timestamp
        trackers.append(meta)
    return trackers

def get_user_dashboard(user_id):  # TODO SORT + REMOVE OLDER ROWS
    trackers = []
    for raw in r_serv_tracker.lrange(f'trackers:user:{user_id}', 0, -1):
        tracker_uuid, timestamp, obj_type, subtype, obj_id = raw.split(':', 4)
        tracker = Tracker(tracker_uuid)
        meta = tracker.get_meta(options={'tags'})
        timestamp = datetime.datetime.fromtimestamp(float(timestamp)).strftime('%Y-%m-%d %H:%M:%S')
        meta['timestamp'] = timestamp
        trackers.append(meta)

    return trackers

def get_trackers_stats(user_id):
    stats = {'all': 0}
    for tracker_type in get_trackers_types():
        nb_global = get_nb_global_trackers(tracker_type=tracker_type)
        nb_user = get_nb_user_trackers(user_id, tracker_type=tracker_type)
        stats[tracker_type] = nb_global + nb_user
        stats['all'] += nb_global + nb_user
    return stats



## Cache ##
# TODO API: Check Tracker type
def trigger_trackers_refresh(tracker_type):
    r_cache.set(f'tracker:refresh:{tracker_type}', time.time())

def get_tracker_last_updated_by_type(tracker_type):
    epoch_update = r_cache.get(f'tracker:refresh:{tracker_type}')
    if not epoch_update:
        epoch_update = 0
    return float(epoch_update)
# - Cache - #



# Dashboard by user -> tracker
        # Add get last tracker in User class

# Global/User dashboard last trackers

# -> in ADD function















## Objects ##

def is_obj_tracked(obj_type, subtype, obj_id):
    return r_serv_tracker.exists(f'obj:trackers:{obj_type}:{subtype}:{obj_id}')

def get_obj_trackers(obj_type, subtype, obj_id):
    return r_serv_tracker.smembers(f'obj:trackers:{obj_type}:{subtype}:{obj_id}')

def delete_obj_trackers(obj_type, subtype, obj_id):
    for tracker_uuid in get_obj_trackers(obj_type, subtype, obj_id):
        tracker = Tracker(tracker_uuid)
        tracker.remove(obj_type, subtype, obj_id)

######################
#### TRACKERS ACL ####

## LEVEL ##
def is_tracked_in_global_level(tracked, tracker_type):
    for tracker_uuid in get_trackers_by_tracked(tracker_type, tracked):
        tracker = Tracker(tracker_uuid)
        if tracker.is_level_global():
            return True
    return False

def is_tracked_in_user_level(tracked, tracker_type, user_id):
    trackers_uuid = get_user_trackers_by_tracked(tracker_type, tracked, user_id)
    if trackers_uuid:
        return True
    else:
        return False

## API ##
def api_check_tracker_uuid(tracker_uuid):
    if not is_valid_uuid_v4(tracker_uuid):
        return {"status": "error", "reason": "Invalid uuid"}, 400
    if not r_serv_tracker.exists(f'tracker:{tracker_uuid}'):
        return {"status": "error", "reason": "Unknown uuid"}, 404
    return None

def api_check_tracker_acl(tracker_uuid, user_id):
    res = api_check_tracker_uuid(tracker_uuid)
    if res:
        return res
    tracker = Tracker(tracker_uuid)
    if tracker.is_level_user():
        if tracker.get_user() != user_id or not User(user_id).is_in_role('admin'):
            return {"status": "error", "reason": "Access Denied"}, 403
    return None

def api_is_allowed_to_edit_tracker(tracker_uuid, user_id):
    if not is_valid_uuid_v4(tracker_uuid):
        return {"status": "error", "reason": "Invalid uuid"}, 400
    tracker_creator = r_serv_tracker.hget('tracker:{}'.format(tracker_uuid), 'user_id')
    if not tracker_creator:
        return {"status": "error", "reason": "Unknown uuid"}, 404
    user = User(user_id)
    if not user.is_in_role('admin') and user_id != tracker_creator:
        return {"status": "error", "reason": "Access Denied"}, 403
    return {"uuid": tracker_uuid}, 200

##-- ACL --##

#### FIX DB #### TODO ###################################################################
def fix_tracker_stats_per_day(tracker_uuid):
    tracker = Tracker(tracker_uuid)
    date_from = tracker.get_date()
    date_to = Date.get_today_date_str()
    # delete stats
    r_serv_tracker.delete(f'tracker:stat:{tracker_uuid}')
    r_serv_tracker.hdel(f'tracker:{tracker_uuid}', 'first_seen')
    r_serv_tracker.hdel(f'tracker:{tracker_uuid}', 'last_seen')
    # create new stats
    for date_day in Date.substract_date(date_from, date_to):
        date_day = int(date_day)

        nb_items = r_serv_tracker.scard(f'tracker:item:{tracker_uuid}:{date_day}')
        if nb_items:
            r_serv_tracker.zincrby(f'tracker:stat:{tracker_uuid}', nb_items, int(date_day))

            # update first_seen/last_seen
            tracker.update_daterange(date_day)

def fix_tracker_item_link(tracker_uuid):
    tracker = Tracker(tracker_uuid)
    date_from = tracker.get_first_seen()
    date_to = tracker.get_last_seen()

    if date_from and date_to:
        for date_day in Date.substract_date(date_from, date_to):
            l_items = r_serv_tracker.smembers(f'tracker:item:{tracker_uuid}:{date_day}')
            for item_id in l_items:
                r_serv_tracker.sadd(f'obj:trackers:item:{item_id}', tracker_uuid)

def fix_all_tracker_uuid_list():
    r_serv_tracker.delete(f'trackers:all')
    for tracker_type in get_trackers_types():
        r_serv_tracker.delete(f'trackers:all:{tracker_type}')
        for tracked in get_trackers_tracked_by_type(tracker_type):
            l_tracker_uuid = get_trackers_by_tracked(tracker_type, tracked)
            for tracker_uuid in l_tracker_uuid:
                r_serv_tracker.sadd(f'trackers:all', tracker_uuid)
                r_serv_tracker.sadd(f'trackers:all:{tracker_type}', tracker_uuid)

##-- FIX DB --##

#### CREATE TRACKER ####
def api_validate_tracker_to_add(to_track , tracker_type, nb_words=1):
    if tracker_type=='regex':
        if not is_valid_regex(to_track):
            return {"status": "error", "reason": "Invalid regex"}, 400
    elif tracker_type=='word' or tracker_type=='set':
        # force lowercase
        to_track = to_track.lower()
        word_set = set(to_track)
        set_inter = word_set.intersection(special_characters)
        if set_inter:
            return {"status": "error", "reason": f'special character(s) not allowed: {set_inter}', "message": "Please use a python regex or remove all special characters"}, 400
        words = to_track.split()
        # not a word
        if tracker_type=='word' and len(words)>1:
            tracker_type = 'set'

        # output format: tracker1,tracker2,tracker3;2
        if tracker_type=='set':
            try:
                nb_words = int(nb_words)
            except TypeError:
                nb_words = 1
            if nb_words == 0:
                nb_words = 1

            words_set = set(words)
            words_set = sorted(words_set)
            if nb_words > len(words_set):
                nb_words = len(words_set)

            to_track = ",".join(words_set)
            to_track = f"{to_track};{nb_words}"
    elif tracker_type == 'typosquatting':
        to_track = to_track.lower()
        # Take only the first term
        domain = to_track.split(" ")
        if len(domain) > 1:
            return {"status": "error", "reason": "Only one domain is accepted at a time"}, 400
        if not "." in to_track:
            return {"status": "error", "reason": "Invalid domain name"}, 400

    elif tracker_type=='yara_custom':
        if not is_valid_yara_rule(to_track):
            return {"status": "error", "reason": "Invalid custom Yara Rule"}, 400
    elif tracker_type=='yara_default':
        if not is_valid_default_yara_rule(to_track):
            return {"status": "error", "reason": "The Yara Rule doesn't exist"}, 400
    else:
        return {"status": "error", "reason": "Incorrect type"}, 400
    return {"status": "success", "tracked": to_track, "type": tracker_type}, 200

def api_add_tracker(dict_input, user_id):
    to_track = dict_input.get('tracked', None)
    if not to_track:
        return {"status": "error", "reason": "Tracker not provided"}, 400
    tracker_type = dict_input.get('type', None)
    if not tracker_type:
        return {"status": "error", "reason": "Tracker type not provided"}, 400
    nb_words = dict_input.get('nb_words', 1)
    description = dict_input.get('description', '')
    description = escape(description)
    webhook = dict_input.get('webhook', '')
    webhook = escape(webhook)
    res = api_validate_tracker_to_add(to_track , tracker_type, nb_words=nb_words)
    if res[1]!=200:
        return res
    to_track = res[0]['tracked']
    tracker_type = res[0]['type']

    tags = dict_input.get('tags', [])
    mails = dict_input.get('mails', [])
    res = verify_mail_list(mails)
    if res:
        return res

    # Filters # TODO MOVE ME
    filters = dict_input.get('filters', {})
    if filters:
        if filters.keys() == {'decoded', 'item', 'pgp'} and set(filters['pgp'].get('subtypes', [])) == {'mail', 'name'}:
            filters = {}
        for obj_type in filters:
            if obj_type not in get_objects_tracked():
                return {"status": "error", "reason": "Invalid Tracker Object type"}, 400

            if obj_type == 'pgp':
                if set(filters['pgp'].get('subtypes', [])) == {'mail', 'name'}:
                    filters['pgp'].pop('subtypes')

            for filter_name in filters[obj_type]:
                if filter_name not in {'mimetypes', 'sources', 'subtypes'}:
                    return {"status": "error", "reason": "Invalid Filter"}, 400
                elif filter_name == 'mimetypes': # TODO
                    pass
                elif filter_name == 'sources':
                    if obj_type == 'item':
                        res = item_basic.verify_sources_list(filters['item']['sources'])
                        if res:
                            return res
                    else:
                        return {"status": "error", "reason": "Invalid Filter sources"}, 400
                elif filter_name == 'subtypes':
                    obj_subtypes = set(get_object_all_subtypes(obj_type))
                    for subtype in filters[obj_type]['subtypes']:
                        if subtype not in obj_subtypes:
                            return {"status": "error", "reason": "Invalid Tracker Object subtype"}, 400

    level = dict_input.get('level', 1)
    try:
        level = int(level)
    except TypeError:
        level = 1
    if level not in range(0, 1):
        level = 1

    tracker_uuid = create_tracker(tracker_type, to_track, user_id, level, description=description, filters=filters,
                                  tags=tags, mails=mails, webhook=webhook)

    return {'tracked': to_track, 'type': tracker_type, 'uuid': tracker_uuid}, 200

# TODO
def api_edit_tracker(dict_input, user_id):
    pass
    # tracker_uuid = dict_input.get('uuid', None)
    # # check edit ACL
    # if tracker_uuid:
    #     res = api_is_allowed_to_edit_tracker(tracker_uuid, user_id)
    #     if res[1] != 200:
    #         return res
    # else:
    #     # check if tracker already tracked in global
    #     if level==1:
    #         if is_tracked_in_global_level(to_track, tracker_type) and not tracker_uuid:
    #             return {"status": "error", "reason": "Tracker already exist"}, 409
    #     else:
    #         if is_tracked_in_user_level(to_track, tracker_type, user_id) and not tracker_uuid:
    #             return {"status": "error", "reason": "Tracker already exist"}, 409

def api_delete_tracker(data, user_id):
    tracker_uuid = data.get('uuid')
    res = api_check_tracker_acl(tracker_uuid, user_id)
    if res:
        return res

    tracker = Tracker(tracker_uuid)
    return tracker.delete(), 200




##-- CREATE TRACKER --##

####################
#### WORD - SET ####

def get_words_tracked_list(): # TODO REMOVE ME ????
    return list(r_serv_tracker.smembers('all:tracker:word'))

def get_tracked_words():
    to_track = {}
    for obj_type in get_objects_tracked():
        to_track[obj_type] = _get_tracked_by_obj_type('word', obj_type)
    return to_track

def get_tracked_sets():
    to_track = {}
    for obj_type in get_objects_tracked():
        to_track[obj_type] = []
        for tracked in _get_tracked_by_obj_type('set', obj_type):
            res = tracked.split(';')
            nb_words = int(res[1])
            words_set = res[0].split(',')
            to_track[obj_type].append({'words': words_set, 'nb': nb_words, 'tracked': tracked})
    return to_track

def get_text_word_frequency(content, filtering=True):
    content = content.lower()
    words_dict = defaultdict(int)

    if filtering:
        blob = TextBlob(content, tokenizer=tokenizer)
    else:
        blob = TextBlob(content)
    for word in blob.tokens:
        words_dict[word] += 1
    return words_dict

###############
#### REGEX ####

def get_tracked_regexs():
    to_track = {}
    for obj_type in get_objects_tracked():
        to_track[obj_type] = []
        for tracked in _get_tracked_by_obj_type('regex', obj_type):
            to_track[obj_type].append({'regex': re.compile(tracked), 'tracked': tracked})
    return to_track

########################
#### TYPO SQUATTING ####

def get_tracked_typosquatting_domains(tracked):
    return r_serv_tracker.smembers(f'tracker:typosquatting:{tracked}')

def get_tracked_typosquatting():
    to_track = {}
    for obj_type in get_objects_tracked():
        to_track[obj_type] = []
        for tracked in _get_tracked_by_obj_type('typosquatting', obj_type):
            to_track[obj_type].append({'domains': get_tracked_typosquatting_domains(tracked), 'tracked': tracked})
    return to_track

##############
#### YARA ####
def get_yara_rules_dir():
    return os.path.join(os.environ['AIL_BIN'], 'trackers', 'yara')

def get_yara_rules_default_dir():
    return os.path.join(os.environ['AIL_BIN'], 'trackers', 'yara', 'ail-yara-rules', 'rules')

# # TODO: cache + update
def get_all_default_yara_rules_types():
    yara_dir = get_yara_rules_default_dir()
    all_yara_types = next(os.walk(yara_dir))[1]
    # save in cache ?
    return all_yara_types

# # TODO: cache + update
def get_all_default_yara_files():
    yara_dir = get_yara_rules_default_dir()
    all_default_yara_files = {}
    for rules_type in get_all_default_yara_rules_types():
        all_default_yara_files[rules_type] = os.listdir(os.path.join(yara_dir, rules_type))
    return all_default_yara_files

def get_all_default_yara_rules_by_type(yara_types):
    all_default_yara_files = get_all_default_yara_files()
    if yara_types in all_default_yara_files:
        return all_default_yara_files[yara_types]
    else:
        return []

def get_all_tracked_yara_files(filter_disabled=False):
    yara_files = r_serv_tracker.smembers('all:tracker:yara')
    if not yara_files:
        yara_files = []
    if filter_disabled:
        pass
    return yara_files

def get_tracked_yara_rules():
    to_track = {}
    for obj_type in get_objects_tracked():
        rules = {}
        for tracked in _get_tracked_by_obj_type('yara', obj_type):
            rules[tracked] = os.path.join(get_yara_rules_dir(), tracked)
        to_track[obj_type] = yara.compile(filepaths=rules)
    print(to_track)
    return to_track

def reload_yara_rules():
    yara_files = get_all_tracked_yara_files()
    # {uuid: filename}
    rule_dict = {}
    for yar_path in yara_files:
        for tracker_uuid in get_trackers_by_tracked('yara', yar_path):
            rule_dict[tracker_uuid] = os.path.join(get_yara_rules_dir(), yar_path)
    for tracker_uuid in rule_dict:
        if not os.path.isfile(rule_dict[tracker_uuid]):
            # TODO IGNORE + LOGS
            raise Exception(f"Error: {rule_dict[tracker_uuid]} doesn't exists")
    rules = yara.compile(filepaths=rule_dict)
    return rules

def is_valid_yara_rule(yara_rule):
    try:
        yara.compile(source=yara_rule)
        return True
    except:
        return False

def is_default_yara_rule(tracked_yara_name):
    yara_dir = get_yara_rules_dir()
    filename = os.path.join(yara_dir, tracked_yara_name)
    filename = os.path.realpath(filename)
    try:
        if tracked_yara_name.split('/')[0] == 'custom-rules':
            return False
    except:
        return False
    if not os.path.commonprefix([filename, yara_dir]) == yara_dir:
        return False
    else:
        if os.path.isfile(filename):
            return True
    return False

def is_valid_default_yara_rule(yara_rule, verbose=True):
    yara_dir = get_yara_rules_default_dir()
    filename = os.path.join(yara_dir, yara_rule)
    filename = os.path.realpath(filename)
    # incorrect filename
    if not os.path.commonprefix([filename, yara_dir]) == yara_dir:
        if verbose:
            print('error: file transversal')
            print(yara_dir)
            print(filename)
        return False
    else:
        if os.path.isfile(filename):
            return True
        else:
            return False

def save_yara_rule(yara_rule_type, yara_rule, tracker_uuid=None):
    if yara_rule_type == 'yara_custom':
        if not  tracker_uuid:
            tracker_uuid = str(uuid.uuid4())
        filename = os.path.join('custom-rules', tracker_uuid + '.yar')
        with open(os.path.join(get_yara_rules_dir(), filename), 'w') as f:
            f.write(str(yara_rule))
    if yara_rule_type == 'yara_default':
        filename = os.path.join('ail-yara-rules', 'rules', yara_rule)
    return filename

def get_yara_rule_file_by_tracker_name(tracked_yara_name):
    yara_dir = get_yara_rules_dir()
    filename = os.path.join(yara_dir, tracked_yara_name)
    filename = os.path.realpath(filename)
    if not os.path.commonprefix([filename, yara_dir]) == yara_dir:
        print('error: file transversal')
        print(yara_dir)
        print(filename)
        return None
    return filename

def get_yara_rule_content(yara_rule):
    yara_dir = get_yara_rules_dir()
    filename = os.path.join(yara_dir, yara_rule)
    filename = os.path.realpath(filename)

    # incorrect filename
    if not os.path.commonprefix([filename, yara_dir]) == yara_dir:
        return '' # # TODO: throw exception

    with open(filename, 'r') as f:
        rule_content = f.read()
    return rule_content

def api_get_default_rule_content(default_yara_rule):
    yara_dir = get_yara_rules_default_dir()
    filename = os.path.join(yara_dir, default_yara_rule)
    filename = os.path.realpath(filename)
    if not os.path.commonprefix([filename, yara_dir]) == yara_dir:
        return {'status': 'error', 'reason': 'file traversal detected'}, 400

    if not os.path.isfile(filename):
        return {'status': 'error', 'reason': 'yara rule not found'}, 400

    with open(filename, 'r') as f:
        rule_content = f.read()
    return {'rule_name': default_yara_rule, 'content': rule_content}, 200


def get_yara_rule_content_restapi(request_dict):
    rule_name = request_dict.get('rule_name', None)
    if not request_dict:
        return {'status': 'error', 'reason': 'Malformed JSON'}, 400
    if not rule_name:
        return {'status': 'error', 'reason': 'Mandatory parameter(s) not provided'}, 400
    yara_dir = get_yara_rules_dir()
    filename = os.path.join(yara_dir, rule_name)
    filename = os.path.realpath(filename)
    if not os.path.commonprefix([filename, yara_dir]) == yara_dir:
        return {'status': 'error', 'reason': 'File Path Traversal'}, 400
    if not os.path.isfile(filename):
        return {'status': 'error', 'reason': 'yara rule not found'}, 400
    with open(filename, 'r') as f:
        rule_content = f.read()
    rule_content = base64.b64encode((rule_content.encode('utf-8'))).decode('UTF-8')
    return {'status': 'success', 'content': rule_content}, 200



##-- YARA --##

######################
#### RETRO - HUNT ####

# state: pending/running/completed/paused

# task keys:
## tracker:retro_hunt:task:{task_uuid}          state
#                                               start_time
#                                               end_time
#                                               date_from
#                                               date_to
#                                               creator
#                                               timeout
#                                               date
#                                               type

class RetroHunt:

    def __init__(self, task_uuid):
        self.uuid = task_uuid

    def exists(self):
        return r_serv_tracker.exists(f'tracker:retro_hunt:task:{self.uuid}')

    def _set_field(self, field, value):
        return r_serv_tracker.hset(f'tracker:retro_hunt:task:{self.uuid}', field, value)

    def get_creator(self):
        return r_serv_tracker.hget(f'tracker:retro_hunt:task:{self.uuid}', 'creator')

    def get_date(self):
        return r_serv_tracker.hget(f'tracker:retro_hunt:task:{self.uuid}', 'date')

    def get_date_from(self):
        return r_serv_tracker.hget(f'tracker:retro_hunt:task:{self.uuid}', 'date_from')

    def get_date_to(self):
        return r_serv_tracker.hget(f'tracker:retro_hunt:task:{self.uuid}', 'date_to')

    def get_last_analyzed(self):
        return r_serv_tracker.hget(f'tracker:retro_hunt:task:{self.uuid}', 'last')

    def get_name(self):
        return r_serv_tracker.hget(f'tracker:retro_hunt:task:{self.uuid}', 'name')

    def get_description(self):
        return r_serv_tracker.hget(f'tracker:retro_hunt:task:{self.uuid}', 'description')

    def get_timeout(self):
        res = r_serv_tracker.hget(f'tracker:retro_hunt:task:{self.uuid}', 'timeout')
        if res:
            return int(res)
        else:
            return 30  # # TODO: FIXME use instance limit

    def get_sources(self, r_sort=False): # TODO ADAPT TO ALL OBJECTS ???
        sources = r_serv_tracker.smembers(f'tracker:retro_hunt:task:sources:{self.uuid}')
        if not sources:
            sources = set(item_basic.get_all_items_sources(filter_dir=False))
        if r_sort:
            sources = sorted(sources)
        return sources

    def get_tags(self):
        return r_serv_tracker.smembers(f'tracker:retro_hunt:task:tags:{self.uuid}')

    def get_mails(self):
        return r_serv_tracker.smembers(f'tracker:retro_hunt:task:mails:{self.uuid}')

    def get_state(self):
        return r_serv_tracker.hget(f'tracker:retro_hunt:task:{self.uuid}', 'state')

    def _set_state(self, new_state):
        curr_state = self.get_state()
        if curr_state:
            r_serv_tracker.srem(f'tracker:retro_hunt:task:{curr_state}', self.uuid)
        r_serv_tracker.sadd(f'tracker:retro_hunt:task:{new_state}', self.uuid)
        r_serv_tracker.hset(f'tracker:retro_hunt:task:{self.uuid}', 'state', new_state)

    def get_rule(self, r_compile=False):
        rule = r_serv_tracker.hget(f'tracker:retro_hunt:task:{self.uuid}', 'rule')
        if r_compile:
            rule = os.path.join(get_yara_rules_dir(), rule)
            rule_dict = {self.uuid: os.path.join(get_yara_rules_dir(), rule)}
            rule = yara.compile(filepaths=rule_dict)
        return rule

    # add timeout ?
    def get_meta(self, options=set()):
        meta = {'uuid': self.uuid,
                'date_from':  self.get_date_from(),
                'date_to': self.get_date_to(),
                'name': self.get_name(),
                'state': self.get_state(),
                'rule': self.get_rule(),
                }
        if 'creator' in options:
            meta['creator'] = self.get_creator()
        if 'date' in options:
                meta['date'] = self.get_date()
        if 'description' in options:
            meta['description'] = self.get_description()
        if 'mails' in options:
            meta['mails'] = self.get_mails()
        if 'nb_match' in options:
            meta['nb_match'] = self.get_nb_match()
        if 'progress' in options:
            meta['progress'] = self.get_progress()
        if 'sources' in options:
            meta['progress'] = self.get_sources(r_sort=True)
        if 'tags' in options:
            meta['tags'] = self.get_tags()
        return meta

    def to_pause(self):
        to_pause = r_cache.hget(f'tracker:retro_hunt:task:{self.uuid}', 'pause')
        if to_pause:
            return True
        else:
            return False

    def pause(self):
        self._set_state('paused')
        r_cache.hset(f'tracker:retro_hunt:task:{self.uuid}', 'pause', time.time())
        self.clear_cache()

    def resume(self):
        r_cache.hdel(f'tracker:retro_hunt:task:{self.uuid}', 'pause')
        self._set_state('pending')

    def run(self): # TODO ADD MORE CHECK
        self._set_state('running')

    def complete(self):
        self._set_state('completed')
        self.update_nb_match()
        self.clear_cache()

    def get_progress(self):
        if self.get_state() == 'completed':
            progress = 100
        else:
            progress = r_cache.hget(f'tracker:retro_hunt:task:{self.uuid}', 'progress')
            if not progress:
                progress = self.compute_progress()
        return progress

    def compute_progress(self, date_from=None, date_to=None, sources=[], curr_date=None, nb_src_done=0):
        # get nb days
        if not date_from:
            date_from = self.get_date_from()
        if not date_to:
            date_to = self.get_date_to()
        nb_days = Date.get_nb_days_by_daterange(date_from, date_to)

        # nb days completed
        if not curr_date:
            curr_date = get_retro_hunt_task_current_date(task_uuid) ####################################################
        nb_days_done = Date.get_nb_days_by_daterange(date_from, curr_date) - 1

        # sources
        if not sources:
            nb_sources = len(self.get_sources())
        else:
            nb_sources = len(sources)

        # get progress
        progress = ((nb_days_done * nb_sources) + nb_src_done) * 100 / (nb_days * nb_sources)
        return int(progress)

        # # TODO: # FIXME: # Cache

    def set_progress(self, progress):
        r_cache.hset(f'tracker:retro_hunt:task:{self.uuid}', 'progress', progress)

    def get_nb_match(self):
        return r_serv_tracker.hget(f'tracker:retro_hunt:task:{self.uuid}', 'nb_match')

    def _set_nb_match(self, nb_match):
        r_serv_tracker.hset(f'tracker:retro_hunt:task:{self.uuid}', 'nb_match', nb_match)

    def update_nb_match(self):
        l_date_value = r_serv_tracker.zrange(f'tracker:retro_hunt:task:stat:{self.uuid}', 0, -1, withscores=True)
        nb_match = 0
        for row in l_date_value:
            nb_match += int(row[1])
        self._set_nb_match(nb_match)

    def clear_cache(self):
        r_cache.delete(f'tracker:retro_hunt:task:{self.uuid}')

    def create(self, name, rule, date_from, date_to, creator, description=None, mails=[], tags=[], timeout=30, sources=[], state='pending'):
        if self.exists():
            raise Exception('Error: Retro Hunt Task already exists')

        self._set_field('name', escape(name))

        self._set_field('rule', rule) # TODO FORMAT ???

        self._set_field('date', datetime.date.today().strftime("%Y%m%d"))
        self._set_field('name', escape(name))
        self._set_field('date_from', date_from)
        self._set_field('date_to', date_to)
        self._set_field('creator', creator)
        if description:
            self._set_field('description', description)
        if timeout:
            self._set_field('timeout', int(timeout))
        for source in sources:
            r_serv_tracker.sadd(f'tracker:retro_hunt:task:sources:{self.uuid}', escape(source))
        for tag in tags:
            tag = escape(tag)
            r_serv_tracker.sadd(f'tracker:retro_hunt:task:tags:{self.uuid}', tag)
            Tag.create_custom_tag(tag)
        for mail in mails:
            r_serv_tracker.sadd(f'tracker:retro_hunt:task:mails:{self.uuid}', escape(mail))

        r_serv_tracker.sadd('tracker:retro_hunt:task:all', self.uuid)

        # add to pending tasks
        if state not in ('pending', 'completed', 'paused'):
            state = 'pending'
        self._set_state(state)


    # TODO Delete Rule
    def delete(self):
        if r_serv_tracker.sismember('tracker:retro_hunt:task:running', self.uuid):
            return None

        r_serv_tracker.srem('tracker:retro_hunt:task:pending', self.uuid)
        r_serv_tracker.delete(f'tracker:retro_hunt:task:{self.uuid}')
        r_serv_tracker.delete(f'tracker:retro_hunt:task:sources:{self.uuid}')
        r_serv_tracker.delete(f'tracker:retro_hunt:task:tags:{self.uuid}')
        r_serv_tracker.delete(f'tracker:retro_hunt:task:mails:{self.uuid}')

        for item_date in get_retro_hunt_all_item_dates(task_uuid): ############################ TODO OBJ #######################
            r_serv_tracker.delete(f'tracker:retro_hunt:task:item:{self.uuid}:{item_date}')

        r_serv_tracker.srem('tracker:retro_hunt:task:all', self.uuid)
        r_serv_tracker.srem('tracker:retro_hunt:task:pending', self.uuid)
        r_serv_tracker.srem('tracker:retro_hunt:task:paused', self.uuid)
        r_serv_tracker.srem('tracker:retro_hunt:task:completed', self.uuid)

        self.clear_cache()
        return self.uuid

def create_retro_hunt(name, rule_type, rule, date_from, date_to, creator, description=None, mails=[], tags=[], timeout=30, sources=[], state='pending', task_uuid=None):
    if not task_uuid:
        task_uuid = str(uuid.uuid4())
    retro_hunt = RetroHunt(task_uuid)
    # rule_type: yara_default - yara custom
    rule = save_yara_rule(rule_type, rule, tracker_uuid=retro_hunt.uuid)
    retro_hunt.create(name, rule, date_from, date_to, creator, description=description, mails=mails, tags=tags,
                      timeout=timeout, sources=sources, state=state)
    return retro_hunt.uuid

## ? ? ?
# set tags
# set mails
# limit mail

# SET Retro Hunts

def get_all_retro_hunt_tasks():
    return r_serv_tracker.smembers('tracker:retro_hunt:task:all')

def get_retro_hunt_pending_tasks():
    return r_serv_tracker.smembers('tracker:retro_hunt:task:pending')

def get_retro_hunt_running_tasks():
    return r_serv_tracker.smembers('tracker:retro_hunt:task:running')

def get_retro_hunt_paused_tasks():
    return r_serv_tracker.smembers('tracker:retro_hunt:task:paused')

def get_retro_hunt_completed_tasks():
    return r_serv_tracker.smembers('tracker:retro_hunt:task:completed')

## Change STATES ##

def get_retro_hunt_task_to_start():
    task_uuid = r_serv_tracker.spop('tracker:retro_hunt:task:pending')
    if task_uuid:
        retro_hunt = RetroHunt(task_uuid)
        retro_hunt.run()
    return task_uuid

## Metadata ##

def get_retro_hunt_tasks_metas():
    tasks = []
    for task_uuid in get_all_retro_hunt_tasks():
        retro_hunt = RetroHunt(task_uuid)
        tasks.append(retro_hunt.get_meta(options={'date', 'progress', 'nb_match', 'tags'}))
    return tasks







def get_retro_hunt_last_analyzed(task_uuid):
    return r_serv_tracker.hget(f'tracker:retro_hunt:task:{task_uuid}', 'last')

# Keep history to relaunch on error/pause
def set_retro_hunt_last_analyzed(task_uuid, last_id):
    r_serv_tracker.hset(f'tracker:retro_hunt:task:{task_uuid}', 'last', last_id)

####################################################################################
####################################################################################
####################################################################################
####################################################################################

def set_cache_retro_hunt_task_id(task_uuid, id):
    r_cache.hset(f'tracker:retro_hunt:task:{task_uuid}', 'id', id)

# Others

#                                               date
#                                               type
# tags
# mails
# name
# description

# state error

# TODO
def _re_create_retro_hunt_task(name, rule, date, date_from, date_to, creator, sources, tags, mails, timeout, description, task_uuid, state='pending', nb_match=0, last_id=None):
    retro_hunt = RetroHunt(task_uuid)
    retro_hunt.create(name, rule, date_from, date_to, creator, description=description, mails=mails, tags=tags,
                      timeout=timeout, sources=sources, state=state)
    # TODO
    if last_id:
        set_retro_hunt_last_analyzed(task_uuid, last_id)
    retro_hunt._set_nb_match(nb_match)
    retro_hunt._set_field('date', date)

def get_retro_hunt_task_current_date(task_uuid):
    retro_hunt = RetroHunt(task_uuid)
    last = get_retro_hunt_last_analyzed(task_uuid)
    if last:
        curr_date = item_basic.get_item_date(last)
    else:
        curr_date = retro_hunt.get_date_from()
    return curr_date

def get_retro_hunt_task_nb_src_done(task_uuid, sources=[]):
    retro_hunt = RetroHunt(task_uuid)
    if not sources:
        sources = list(retro_hunt.get_sources(r_sort=True))
    else:
        sources = list(sources)
    last_id = get_retro_hunt_last_analyzed(task_uuid)
    if last_id:
        last_source = item_basic.get_source(last_id)
        try:
            nb_src_done = sources.index(last_source)
        except ValueError:
            nb_src_done = 0
    else:
        nb_src_done = 0
    return nb_src_done

def get_retro_hunt_dir_day_to_analyze(task_uuid, date, filter_last=False, sources=[]):
    retro_hunt = RetroHunt(task_uuid)
    if not sources:
        sources = retro_hunt.get_sources(r_sort=True)

    # filter last
    if filter_last:
        last = get_retro_hunt_last_analyzed(task_uuid)
        if last:
            curr_source = item_basic.get_source(last)
            # remove processed sources
            set_sources = sources.copy()
            for source in sources:
                if source != curr_source:
                    set_sources.remove(source)
                else:
                    break
            sources = set_sources

    # return all dirs by day
    date = f'{date[0:4]}/{date[4:6]}/{date[6:8]}'
    dirs = set()
    for source in sources:
        dirs.add(os.path.join(source, date))
    return dirs

# # TODO: move me
def get_items_to_analyze(dir, last=None):
    if items_dir == 'PASTES':
        full_dir = os.path.join(os.environ['AIL_HOME'], 'PASTES', dir)
    else:
        full_dir = os.path.join(items_dir, dir)
    if os.path.isdir(full_dir):
        all_items = sorted([os.path.join(dir, f) for f in os.listdir(full_dir) if os.path.isfile(os.path.join(full_dir, f))])
        # remove processed items
        if last:
            items_set = all_items.copy()
            for item in all_items:
                if item != last:
                    items_set.remove(item)
                else:
                    break
            all_items = items_set
        return all_items
    else:
        return []

# # TODO: ADD MAP ID => Retro_Hunt
def save_retro_hunt_match(task_uuid, id, object_type='item'):
    item_date = item_basic.get_item_date(id)
    res = r_serv_tracker.sadd(f'tracker:retro_hunt:task:item:{task_uuid}:{item_date}', id)
    # track nb item by date
    if res == 1:
        r_serv_tracker.zincrby(f'tracker:retro_hunt:task:stat:{task_uuid}', 1, int(item_date))
    # Add map obj_id -> task_uuid
    r_serv_tracker.sadd(f'obj:retro_hunt:item:{id}', task_uuid)

def delete_retro_hunt_obj(task_uuid, obj_type, obj_id):
    item_date = item_basic.get_item_date(obj_id)
    res = r_serv_tracker.srem(f'tracker:retro_hunt:task:item:{task_uuid}:{item_date}', obj_id)
    get_retro_hunt_nb_item_by_day()
    # track nb item by date
    if res == 1:
        r_serv_tracker.zincrby(f'tracker:retro_hunt:task:stat:{task_uuid}', -1, int(item_date))
    # Add map obj_id -> task_uuid
    r_serv_tracker.srem(f'obj:retro_hunt:item:{obj_id}', task_uuid)

# TODO
def delete_object_reto_hunts(obj_type, obj_id):
    pass
#     # get items all retro hunts
#     for task_uuid in : #############################################
#         delete_retro_hunt_obj(task_uuid, obj_type, obj_id)

def get_retro_hunt_all_item_dates(task_uuid):
    return r_serv_tracker.zrange(f'tracker:retro_hunt:task:stat:{task_uuid}', 0, -1)

def get_retro_hunt_items_by_daterange(task_uuid, date_from, date_to):
    all_item_id = set()
    if date_from and date_to:
        l_date_match = r_serv_tracker.zrange(f'tracker:retro_hunt:task:stat:{task_uuid}', 0, -1, withscores=True)
        if l_date_match:
            dict_date_match = dict(l_date_match)
            for date_day in Date.substract_date(date_from, date_to):
                if date_day in dict_date_match:
                    all_item_id |= r_serv_tracker.smembers(f'tracker:retro_hunt:task:item:{task_uuid}:{date_day}')
    return all_item_id

def get_retro_hunt_nb_item_by_day(l_task_uuid, date_from=None, date_to=None):
    list_stats = []
    for task_uuid in l_task_uuid:
        dict_task_data = []
        retro_hunt = RetroHunt(task_uuid)

        l_date_match = r_serv_tracker.zrange(f'tracker:retro_hunt:task:stat:{task_uuid}', 0, -1, withscores=True) ########################
        if l_date_match:
            dict_date_match = dict(l_date_match)
            if not date_from:
                date_from = min(dict_date_match)
            if not date_to:
                date_to = max(dict_date_match)

            date_range = Date.substract_date(date_from, date_to)
            for date_day in date_range:
                nb_seen_this_day = int(dict_date_match.get(date_day, 0))
                dict_task_data.append({"date": date_day,"value": int(nb_seen_this_day)})
            list_stats.append({"name": retro_hunt.get_name(),"Data": dict_task_data})
    return list_stats

## API ##
def api_check_retro_hunt_task_uuid(task_uuid):
    if not is_valid_uuid_v4(task_uuid):
        return {"status": "error", "reason": "Invalid uuid"}, 400
    if not r_serv_tracker.exists(f'tracker:retro_hunt:task:{task_uuid}'):
        return {"status": "error", "reason": "Unknown uuid"}, 404
    return None

def api_get_retro_hunt_items(dict_input):
    task_uuid = dict_input.get('uuid', None)
    res = api_check_retro_hunt_task_uuid(task_uuid)
    if res:
        return res

    retro_hunt = RetroHunt(task_uuid)

    # TODO SANITIZE DATES
    date_from = dict_input.get('date_from', None)
    date_to = dict_input.get('date_to', None)
    if date_from is None:
        date_from = retro_hunt.get_date_from()
    if date_to is None:
        date_to = date_from
    if date_from > date_to:
        date_from = date_to

    all_items_id = get_retro_hunt_items_by_daterange(task_uuid, date_from, date_to)
    all_items_id = item_basic.get_all_items_metadata_dict(all_items_id)

    res_dict = {'uuid': task_uuid,
                'date_from': date_from,
                'date_to': date_to,
                'items': all_items_id}
    return res_dict, 200

def api_pause_retro_hunt_task(task_uuid):
    res = api_check_retro_hunt_task_uuid(task_uuid)
    if res:
        return res
    retro_hunt = RetroHunt(task_uuid)
    task_state = retro_hunt.get_state()
    if task_state not in ['pending', 'running']:
        return {"status": "error", "reason": f"Task {task_uuid} not paused, current state: {task_state}"}, 400
    retro_hunt.pause()
    return task_uuid, 200

def api_resume_retro_hunt_task(task_uuid):
    res = api_check_retro_hunt_task_uuid(task_uuid)
    if res:
        return res
    retro_hunt = RetroHunt(task_uuid)
    if not r_serv_tracker.sismember('tracker:retro_hunt:task:paused', task_uuid):
        return {"status": "error", "reason": f"Task {task_uuid} not paused, current state: {retro_hunt.get_state()}"}, 400
    retro_hunt.resume()
    return task_uuid, 200

def api_validate_rule_to_add(rule, rule_type):
    if rule_type=='yara_custom':
        if not is_valid_yara_rule(rule):
            return {"status": "error", "reason": "Invalid custom Yara Rule"}, 400
    elif rule_type=='yara_default':
        if not is_valid_default_yara_rule(rule):
            return {"status": "error", "reason": "The Yara Rule doesn't exist"}, 400
    else:
        return {"status": "error", "reason": "Incorrect type"}, 400
    return {"status": "success", "rule": rule, "type": rule_type}, 200

def api_create_retro_hunt_task(dict_input, creator):
    # # TODO: API: check mandatory arg
    # # TODO: TIMEOUT

    # timeout=30
    rule = dict_input.get('rule', None)
    if not rule:
        return {"status": "error", "reason": "Retro Hunt Rule not provided"}, 400
    task_type = dict_input.get('type', None)
    if not task_type:
        return {"status": "error", "reason": "type not provided"}, 400

    # # TODO: limit
    name = dict_input.get('name', '')
    name = escape(name)
    name = name[:60]
    # # TODO: limit
    description = dict_input.get('description', '')
    description = escape(description)
    description = description[:1000]

    res = api_validate_rule_to_add(rule , task_type)
    if res[1]!=200:
        return res

    tags = dict_input.get('tags', [])
    mails = dict_input.get('mails', [])
    res = verify_mail_list(mails)
    if res:
        return res

    sources = dict_input.get('sources', [])
    res = item_basic.verify_sources_list(sources)
    if res:
        return res

    date_from = dict_input.get('date_from', '')
    date_to = dict_input.get('date_to', '')
    res = Date.api_validate_str_date_range(date_from, date_to)
    if res:
        return res

    task_uuid = create_retro_hunt(name, task_type, rule, date_from, date_to, creator, description=description,
                                  mails=mails, tags=tags, timeout=30, sources=sources)
    return {'name': name, 'rule': rule, 'type': task_type, 'uuid': task_uuid}, 200

def api_delete_retro_hunt_task(task_uuid):
    res = api_check_retro_hunt_task_uuid(task_uuid)
    if res:
        return res
    if r_serv_tracker.sismember('tracker:retro_hunt:task:running', task_uuid):
        return {"status": "error", "reason": "You can't delete a running task"}, 400
    else:
        retro_hunt = RetroHunt(task_uuid)
        return retro_hunt.delete(), 200

#### DB FIX ####
def get_trackers_tags():
    tags = set()
    for tracker_uuid in get_trackers():
        tracker = Tracker(tracker_uuid)
        for tag in tracker.get_tags():
            tags.add(tag)
    for task_uuid in get_all_retro_hunt_tasks():
        retro_hunt = RetroHunt(task_uuid)
        for tag in retro_hunt.get_tags():
            tags.add(tag)
    return tags

def _fix_db_custom_tags():
    for tag in get_trackers_tags():
        if not Tag.is_taxonomie_tag(tag) and not Tag.is_galaxy_tag(tag):
            Tag.create_custom_tag(tag)

#### -- ####

if __name__ == '__main__':

    _fix_db_custom_tags()
    # fix_all_tracker_uuid_list()
    # res = get_all_tracker_uuid()
    # print(len(res))

    # import Term
    # Term.delete_term('5262ab6c-8784-4a55-b0ff-a471018414b4')

    #fix_tracker_stats_per_day('5262ab6c-8784-4a55-b0ff-a471018414b4')

    # tracker_uuid = '5262ab6c-8784-4a55-b0ff-a471018414b4'
    # fix_tracker_item_link(tracker_uuid)
    # res = get_item_all_trackers_uuid('archive/')
    # print(res)

    #res = is_valid_yara_rule('rule dummy {  }')

    # res = create_tracker('test', 'word', 'admin@admin.test', 1, [], [], None, sources=['crawled', 'pastebin.com', 'rt/pastebin.com'])
    #res = create_tracker('circl\.lu', 'regex', 'admin@admin.test', 1, [], [], None, sources=['crawled','pastebin.com'])
    #print(res)

    #t_uuid = '1c2d35b0-9330-4feb-b454-da13007aa9f7'
    #res = get_tracker_sources('ail-yara-rules/rules/crypto/certificate.yar', 'yara')

    # sys.path.append(os.environ['AIL_BIN'])
    # from packages import Term
    # Term.delete_term('074ab4be-6049-45b5-a20e-8125a4e4f500')


    #res = get_items_to_analyze('archive/pastebin.com_pro/2020/05/15', last='archive/pastebin.com_pro/2020/05/15/zkHEgqjQ.gz')
    #get_retro_hunt_task_progress('0', nb_src_done=2)

    #res = set_cache_retro_hunt_task_progress('0', 100)
    #res = get_retro_hunt_task_nb_src_done('0', sources=['pastebin.com_pro', 'alerts/pastebin.com_pro', 'crawled'])
    #print(res)

    # sources = ['pastebin.com_pro', 'alerts/pastebin.com_pro', 'crawled']
    # rule = 'custom-rules/4a8a3d04-f0b6-43ce-8e00-bdf47a8df241.yar'
    # name = 'retro_hunt_test_1'
    # description = 'circl retro hunt first test'
    # tags =  ['retro_circl', 'circl']
    # creator = 'admin@admin.test'
    # date_from = '20200610'
    # date_to = '20210630'

    #res = create_retro_hunt_task(name, rule, date_from, date_to, creator, sources=sources, tags=tags, description=description)


    #get_retro_hunt_nb_item_by_day(['80b402ef-a8a9-4e97-adb6-e090edcfd571'], date_from=None, date_to=None, num_day=31)

    #res = get_retro_hunt_nb_item_by_day(['c625f971-16e6-4331-82a7-b1e1b9efdec1'], date_from='20200610', date_to='20210630')

    #res = delete_retro_hunt_task('598687b6-f765-4f8b-861a-09ad76d0ab34')

    #print(res)
