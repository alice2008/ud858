App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool

## Project ##

#---- Task1 ----#
1. Enpoint API added:
(1) createSession: create new session. typeOfSession is a Enum value defined in models.py. Open to only the conference organizer.
(2) getConferenceSessions: get all sessions for given conference.
(3) getConferenceSessionsByType: get all sessions for one sessionType for given conference
(4) getSessionsBySpeaker: get all sessions by using speakerUserId (email).

2. Implementation of session and speaker. Session is a app engine date-store kind. Speaker re-uses the Profile kind to store the necessary information.
(1) Session:
	name: name of the session. Required parameter.
    highlights: list of strings.
    speakerName: string
    speakerUserId: speaker email
    duration: integer, representing hours.
    typeOfSession: enum value. Available values (NOT_SPECIFIED, Workshop, Discussion, Lecture, Keynote)
    date: datetime.date(). In "%Y-%m-%d" format.
    startTime: datetime.time(). In "%H:%M:%S" format.
    confWebSafeKey: parent conference websafe key.
    creatorUserId: creator userid (email). Same as the conference userid.
(2) Speaker: re-uses the Profile kind to store the necessary information, includes speakerUserId (Profile.mainEmail) and speakerName (Profile.displayName)

#---- Task2 ----#
1. Endpoint API added:
(1) addSessionToWishlist: add one session to user's SessionWishList. Session websafe key is a query parameter.
(2) deleteSessionInWishlist: delete one session from user's SessionWishList. Session websafe key is a query parameter.
(3) getSessionInWishlist: get all sessions in user's SessionWishList.

2. Comment: modify the Profile kind property by adding sessionsWishList property.

#---- Task3 ----#
1. Endpoint API added:
(1) updateSession: update session information by using session websafe key as query parameter.
(2) deleteSession: delete session from database.
(3) querySessionsByTypeByStartTime: query all sessions by typeOfSession and startTime. In the request, given the disallowed typeOfSession and preferred earliest startTime and latest startTime to filter the results.

2. Comments about querySessionsByTypeByStartTime:
app engine does not allow multiple inequality filters on different property. But we can solve the problem by using one equality filter and one inequality filter. Because typeOfSession is a enum value, by excluding the disallowed session types, we can use equality filter to get sessions for allowed session types. Then using inequality filter to query session in startTime range.

#---- Taks4 ----#
1. Endpoint API added:
(1) getFeaturedSpeaker: get latest feature speaker from memcache. memcache key is 'FSP_'+websafeConferenceKey. websafeConferenceKey is a query parameter. If not such entry existed, return a blank response.

2. Comment:
In this project, I modify the createSession endpoint by adding a new task in taksqueque. Task executing url is '/tasks/cache_feature_speaker' which defeined in main.py. Every time when a new session created, url handler will check if the sessoin speaker is also the speaker in other session in this conference. If the speaker does, he/she will store in memcache as feature speaker.

