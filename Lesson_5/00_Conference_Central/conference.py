#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb
from google.appengine.api import memcache

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize,SessionTypes
from models import StringMessage
from models import Session, SessionPostForm, SessionForm, SessionForms
from models import SessionQueryByTypeByStartTimeForm
from models import FeatureSpeakerForm, FeatureSpeakerForms

from utils import getUserId

from settings import WEB_CLIENT_ID

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_FEATURE_SPEAKERS_KEY = "FEATURE_SPEAKERS"

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

SESSION_DEFAULTS = {
    "duration": 0,
    "highlights": ["Default", "Highlights"],
    "startTime": "00:00:00",
    "date": None,
    "typeOfSession": SessionTypes.NOT_SPECIFIED,
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SESS_GET_BY_TYPE_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession = messages.EnumField(SessionTypes, 2)
)

SESS_POST_REQUEST = endpoints.ResourceContainer(
    SessionPostForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_UPDATE_REQUEST = endpoints.ResourceContainer(
    SessionPostForm,
    websafeSessionKey=messages.StringField(1),
)

SESS_GET_BY_SPEAKER_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speakerUserId = messages.StringField(1, required=True),
)

SESS_GET_BY_KEY_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1, required=True),
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1',
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        # TODO 2: add confirmation email sending task to queue
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )

        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()



        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id =  getUserId(user)
        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
            prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

# TODO 1
    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        # TODO 1
        # return an existing announcement from Memcache or an empty string.
        announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY)
        if not announcement:
            announcement = ''
        return StringMessage(data=announcement)


# - - - Sessions - - - - - - - - - - - - - - - - - - - -

    def _copySessionToForm(self, session, confName):
        """Copy relevant fields from Session to SessionForm"""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(session, field.name):
                if field.name in ['date', 'startTime']:
                    setattr(sf, field.name, str(getattr(session, field.name)))
                else:
                    setattr(sf, field.name, getattr(session, field.name))
            elif field.name == 'websafeKey':
                setattr(sf, field.name, session.key.urlsafe())
        if confName:
            setattr(sf, 'conferenceDisplayName', confName)
        sf.check_initialized()
        return sf

    def _createSpeaker(self, request):
        """creat speaker profile if it does not exist,
        requst is SESS_POST_REQUEST type"""
        # using Profile Kind to store speaker info
        email = getattr(request, 'speakerUserId')
        if hasattr(request, 'speakerName'):
            displayName = getattr(request, 'speakerName')
        else:
            displayName = ''
        speaker_key = ndb.Key(Profile, email)
        speaker_prof = Profile(
            key = speaker_key,
            displayName = displayName,
            mainEmail = email,
        )
        speaker_prof.put()
        return speaker_prof

    # @ndb.transactional(xg=True)
    def _createSessionObject(self, request):
        """create new session entity, requst is SESS_POST_REQUEST type"""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # report error if no session name is given
        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")

        # if speakerName is provided but without speakerUserId. Raise exception
        if request.speakerName and not request.speakerUserId:
            raise endpoints.BadRequestException("Session speakerName must come with speakerUserId")

        # copy SessionForm into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeConferenceKey']

        # set default value for missing items in session model
        for df in SESSION_DEFAULTS:
            if data[df] in (None, []):
                data[df] = SESSION_DEFAULTS[df]

        # print data

        # get parent conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the conference owner can create session.')

        # check if speaker specified
        if data['speakerUserId']:
            speaker = ndb.Key(Profile, data['speakerUserId']).get()
            # if such speaker has been created in Profile Kind
            if speaker:
                # set or override with the name in the Profile
                data['speakerName'] = speaker.displayName
            else: # create profile if it does not exist
                self._createSpeaker(request)


        # if no date specified for session, use default conf start date
        if data['date'] is None:
            data['date'] = conf.startDate
        else:
            data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()

        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'][:8], "%H:%M:%S").time()

        c_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        session_id = Session.allocate_ids(size=1, parent=c_key)[0]
        session_key = ndb.Key(Session, session_id, parent=c_key)
        data['key'] = session_key
        data['confWebSafeKey'] = request.websafeConferenceKey
        data['creatorUserId'] = user_id
        s = Session(**data)
        s.put()

        # check if feature speaker, add to taskqueue
        if s.speakerUserId:
            taskqueue.add(params={'websafeConferenceKey':data['confWebSafeKey'],
                'speakerUserId':data['speakerUserId'], 'speakerName':data['speakerName'],
                'sessionName':data['name']}, url='/tasks/cache_feature_speaker')
        return self._copySessionToForm(s, getattr(conf, 'name'))


    @staticmethod
    def _cacheFeatureSpeaker(websafeConferenceKey, speakerUserId, speakerName, sessionName):
        """static method to memcache the latest featured speaker for one conference"""
        data = {}
        data['websafeConferenceKey'] = websafeConferenceKey
        data['speakerUserId'] = speakerUserId
        data['speakerName'] = speakerName
        data['sessionName'] = sessionName
        # print '===data===', data
        # print type(data)
        websafeConferenceKey = data['websafeConferenceKey']
        speakerUserId = data['speakerUserId']
        ## query if session with same speaker exist
        c_key = ndb.Key(urlsafe=websafeConferenceKey)
        q = Session.query(ancestor=c_key)
        # sessions with same speaker
        sessions = q.filter(Session.speakerUserId == data['speakerUserId']).fetch()

        if len(sessions) <= 1: # not a feature speaker; do nothing
            return

        fsf = FeatureSpeakerForm()
        setattr(fsf, 'speakerUserId', data['speakerUserId'])
        setattr(fsf, 'speakerName', data['speakerName'])
        setattr(fsf, 'sessionList', [])

        for s in sessions:
            fsf.sessionList.append(s.name)
        memcache.set('FSP_' + websafeConferenceKey, fsf)


    @endpoints.method(SESS_POST_REQUEST, SessionForm,
            path='session/{websafeConferenceKey}',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new session"""
        return self._createSessionObject(request)

    @endpoints.method(SESS_GET_REQUEST, SessionForms,
        path='session/{websafeConferenceKey}',
        http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Get all session in one conferences"""
        try:
            conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        except:
            raise endpoints.NotFoundException("Invalid key %s" % request.websafeConferenceKey)
        conf = conf_key.get()
        if not conf:
            raise endpoints.NotFoundException("No conference found with key %s" % request.websafeConferenceKey)
        sessions = Session.query(ancestor=conf_key)
        return SessionForms(items=[self._copySessionToForm(s, getattr(conf, 'name')) for s in sessions])


    @endpoints.method(SESS_GET_BY_TYPE_REQUEST, SessionForms,
        path='session/{websafeConferenceKey}/{typeOfSession}',
        http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Get all sessions belong to one specific type in one conference"""
        query_session_type = request.typeOfSession
        try:
            conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        except:
            raise endpoints.NotFoundException("Invalid key %s" % request.websafeConferenceKey)
        conf = conf_key.get()
        if not conf:
            raise endpoints.NotFoundException("No conference found with key %s" % request.websafeConferenceKey)
        sessions = Session.query(ancestor=conf_key)
        sessions = sessions.filter(Session.typeOfSession == query_session_type)
        return SessionForms(items=[self._copySessionToForm(s, getattr(conf, 'name')) for s in sessions])


    @endpoints.method(SESS_GET_BY_SPEAKER_REQUEST, SessionForms,
        path='sessionBySpeaker/{speakerUserId}',
        http_method='GET', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Get all sessions given by a particular speaker, across all conferences"""
        speakerUserId=request.speakerUserId
        speaker = ndb.Key(Profile, speakerUserId).get()
        if not speaker:
            raise endpoints.NotFoundException("No speaker profile found with key %s" % speakerUserId)

        sessions = Session.query(Session.speakerUserId==speakerUserId)
        items = []
        conf_keys = [ndb.Key(urlsafe=s.confWebSafeKey) for s in sessions]
        confs = ndb.get_multi(conf_keys)
        items = []
        i = 0
        for s in sessions:
            items.append(self._copySessionToForm(s, confs[i].name))
            i += 1
        return SessionForms(items=items)


    def _sessionAddOrDeleteToWishList(self, request, add=True):
        """add or delete one session from user's wishList"""
        # return value; True means success; False means fail
        retval = None
        prof = self._getProfileFromUser() # get user profile. If not exist, create one

        #web-safe session key
        wssk = request.websafeSessionKey
        session = ndb.Key(urlsafe=wssk).get()
        if not session:
            raise endpoints.NotFoundException(
                "No session found with key: %s" % wssk)

        if add:
            if wssk in prof.sessionsWishList:
                raise ConflictException (
                    "You already add this session into your wishlist!")

            prof.sessionsWishList.append(wssk)
            retval = True

        else:
            if wssk in prof.sessionsWishList:
                prof.sessionsWishList.remove(wssk)
                retval = True
            else:
                retval = False

        prof.put()
        return BooleanMessage(data=retval)

    @endpoints.method(message_types.VoidMessage, SessionForms,
        path='session_wishlist', http_method='GET',
        name='getSessionsInWishList')
    def getSessionsInWishList(self, request):
        """Get a list of sessions in user's wishlist"""
        prof = self._getProfileFromUser()
        sess_keys = [ndb.Key(urlsafe=wssk) for wssk in prof.sessionsWishList]
        sessions = ndb.get_multi(sess_keys)

        conf_keys = [ndb.Key(urlsafe=s.confWebSafeKey) for s in sessions]
        confs = ndb.get_multi(conf_keys)
        # print 'sessions\n', sessions
        # print 'conferences\n', confs
        assert len(sessions) == len(confs)
        items = []
        for i in range(len(sessions)):
            items.append(self._copySessionToForm(sessions[i], confs[i].name))
        return SessionForms(items=items)


    @endpoints.method(SESS_GET_BY_KEY_REQUEST, BooleanMessage,
        path='addToWishlist/{websafeSessionKey}',
        http_method='GET', name='addSessionToWishlist')
    def addSessionToWishList(self, request):
        """Add one session to user's SessionsWishList"""
        return self._sessionAddOrDeleteToWishList(request)

    @endpoints.method(SESS_GET_BY_KEY_REQUEST, BooleanMessage,
        path='deleteFromWishlist/{websafeSessionKey}',
        http_method='GET', name='deleteSessionInWishlist')
    def deleteSessionInWishList(self, request):
        """Delete one session to user's SessionsWishList"""
        return self._sessionAddOrDeleteToWishList(request, add=False)


    def _deleteSession(self, request):
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        session = ndb.Key(urlsafe=request.websafeSessionKey).get()
        if not session:
            raise endpoints.NotFoundException(
                "No session found with key %s" % request.websafeSessionKey)

        if user_id != session.creatorUserId:
            raise endpoints.ForbiddenException(
                "Only the onwer can delete this session!")
        session.key.delete()
        return BooleanMessage(data=True)

    def _updateSession(self, request):
        """update session. request is SESS_UPDATE_REQUEST type"""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if request.speakerName and not request.speakerUserId:
            raise endpoints.ForbiddenException('Bad request: speakerName cannot come without speakerUserId')

        try:
            session = ndb.Key(urlsafe=request.websafeSessionKey).get()
        except:
            raise endpoints.NotFoundException("Not valid key %s " % request.websafeSessionKey)
        if not session:
            raise endpoints.NotFoundException(
                "No session found with key %s" % request.websafeSessionKey)

        if user_id != session.creatorUserId:
            raise endpoints.ForbiddenException(
                "Only the owner can update this session!")

        # print request
        for field in request.all_fields():
            data = getattr(request, field.name)
            if data:
                if field.name == 'date':
                    data = datetime.strptime(data[:10], "%Y-%m-%d").date()
                elif field.name == 'startTime':
                    data = datetime.strptime(data[:8], "%H:%M:%S").time()
                setattr(session, field.name, data)

        # check if request has speakerUserId, if speaker has not been created, create a new one.
        if hasattr(request, 'speakerUserId'):
            speaker = ndb.Key(Profile, request.speakerUserId).get()
            if not speaker:
                speaker = self._createSpeaker(request)
            # set session speakerName with the one in database
            setattr(session, 'speakerName', speaker.displayName)

        session.put()
        conf = ndb.Key(urlsafe=session.confWebSafeKey).get()
        return self._copySessionToForm(session, conf.name)


    @endpoints.method(SESS_UPDATE_REQUEST, SessionForm,
        path='updateSession/{websafeSessionKey}',
        http_method='POST', name='updateSession')
    def updateSession(self, request):
        """Update session information"""
        return self._updateSession(request)


    @endpoints.method(SESS_GET_BY_KEY_REQUEST, BooleanMessage,
        path='deleteSession/{websafeSessionKey}',
        http_method='POST', name='deleteSession')
    def deleteSession(self, request):
        """Delete session from the datastore"""
        return self._deleteSession(request)

    def _querySessionsByTypeByStartTime(self, request):
        """Query session first by disallowed types and then by start and end time.
        request is SessionQueryByTypeByStartTimeForm type"""
        # request only has disallowed types. Session type is a Enum value.
        # by excluding the disallowed types from list, get preferred session types.
        allowed_session_types = []
        for t in SessionTypes:
            if not t in request.typeOfSessionDisallowed:
                allowed_session_types.append(t)
        # print t
        if request.earliestStartTime:
            earliestStartTime = datetime.strptime(request.earliestStartTime, "%H:%M:%S").time()
        if request.latestStartTime:
            latestStartTime = datetime.strptime(request.latestStartTime, "%H:%M:%S").time()

        # first level of query to get all the sessions in allowed session types.
        q = Session.query(Session.typeOfSession.IN(allowed_session_types))
        # second and third level of queries for startTime and endTime
        if request.earliestStartTime:
            q = q.filter(Session.startTime >= earliestStartTime)
        if request.latestStartTime:
            q = q.filter(Session.startTime <= latestStartTime)
        conf_keys = [(ndb.Key(urlsafe=session.confWebSafeKey)) for session in q]
        confs = ndb.get_multi(conf_keys)
        names = {}
        for i in range(len(confs)):
            names[conf_keys[i].urlsafe()] = confs[i].name

        items = []
        for s in q:
            items.append(self._copySessionToForm(s, names[s.confWebSafeKey]))
        return SessionForms(items=items)


    @endpoints.method(SessionQueryByTypeByStartTimeForm, SessionForms,
        path='querySessionsByTypeByStartTime', http_method='POST',
        name='querySessionsByTypeByStartTime')
    def querySessionsByTypeByStartTime(self, request):
        """Query for sessions by startTime and by type"""
        return self._querySessionsByTypeByStartTime(request)

# ------- Featured speaker --------------------------------------

    @endpoints.method(SESS_GET_REQUEST, FeatureSpeakerForm,
        path='getFeaturedSpeaker/{websafeConferenceKey}', http_method='GET',
        name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Get latest featured Speakers' list from memcache a conference"""
        #memache key is 'FSP_' + websafeConferenceKey
        featured_speaker = memcache.get('FSP_' + request.websafeConferenceKey)
        # print "=== featured_speakers", featured_speaker
        if not featured_speaker:
            featured_speaker = FeatureSpeakerForm()
        return featured_speaker



api = endpoints.api_server([ConferenceApi]) # register API
