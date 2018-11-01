"""
   A barebones AppEngine application that allows the user to login
   with Spotify and then lists their playlists.


   Sean Munson for HCDE 310
"""

#from secrets import CLIENT_ID, CLIENT_SECRET
CLIENT_ID = "1fb6fc59494e4436b1a7b308507251b0"
CLIENT_SECRET = "d6113a31d8c741af8a39e8eac5cf93a4"
GRANT_TYPE = 'authorization_code'

import webapp2, urllib2, os, urllib, json, jinja2, logging, sys, time
import base64, Cookie, hashlib, hmac, email
from google.appengine.ext import db
from google.appengine.api import urlfetch
import spotipy
import spotipy.util as util
import json
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
import time
import musicbrainzngs as mb
from spotipy.oauth2 import SpotifyClientCredentials
import os
from google.appengine.ext import deferred
from google.appengine.runtime import DeadlineExceededError
#this version uses client credentials, does not allow private access for now

#maybe put in method and definitely put in hidden folder
mb.auth("trepaolini", "stewie")
mb.set_useragent("Music Mapper", "0.1", "trepaolini@gmail.com")


JINJA_ENVIRONMENT = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True)

### this is our user database model. We will use it to store the access_token
#somehow find a way to check if a person music count has changed, then update if that is true
class User(db.Model):
    uid = db.StringProperty(required=True)
    displayname = db.StringProperty(required=False)
    img = db.StringProperty(required=False)   
    access_token = db.StringProperty(required=True)
    refresh_token = db.StringProperty(required=False)
    profile_url=db.StringProperty(required=False)
    api_url=db.StringProperty(required=False)
    email = db.StringProperty(required=False)

### helper functions 

### We have some cookie functions here. This lets us be careful 
### to make sure that a  malicious user can't spoof your user ID in
### their cookie and then use our site to do things on your behalf
def set_cookie(response, name, value, domain=None, path="/", expires=None):
    """Generates and signs a cookie for the give name/value"""
    timestamp = str(int(time.time()))
    value = base64.b64encode(value)
    signature = cookie_signature(value, timestamp)
    cookie = Cookie.BaseCookie()
    cookie[name] = "|".join([value, timestamp, signature])
    cookie[name]["path"] = path
    if domain: cookie[name]["domain"] = domain
    if expires:
        cookie[name]["expires"] = email.utils.formatdate(
            expires, localtime=False, usegmt=True)
    response.headers.add("Set-Cookie", cookie.output()[12:])


def parse_cookie(value):
    """Parses and verifies a cookie value from set_cookie"""
    if not value: return None
    parts = value.split("|")
    if len(parts) != 3: return None
    if cookie_signature(parts[0], parts[1]) != parts[2]:
        logging.warning("Invalid cookie signature %r", value)
        return None
    timestamp = int(parts[1])
    if timestamp < time.time() - 30 * 86400:
        logging.warning("Expired cookie %r", value)
        return None
    try:
        return base64.b64decode(parts[0]).strip()
    except:
        return None

def cookie_signature(*parts):
    """
    Generates a cookie signature.

    We use the Spotify app secret since it is different for every app (so
    people using this example don't accidentally all use the same secret).
    """
    chash = hmac.new(CLIENT_SECRET, digestmod=hashlib.sha1)
    for part in parts: chash.update(part)
    return chash.hexdigest()
    
    
### this adds a header with the user's access_token to Spotify requests
def spotifyurlfetch(url,access_token,params=None):
    headers = {'Authorization': 'Bearer '+access_token}
    response = urlfetch.fetch(url,method=urlfetch.GET, payload=params, headers=headers)
    return response.content


### handlers

### this handler will be our Base Handler -- it checks for the current user. 
### creating this class allows our other classes to inherit from it
### so they all "know about" the user
class BaseHandler(webapp2.RequestHandler):
    # @property followed by def current_user makes so that if x is an instance
    # of BaseHandler, x.current_user can be referred to, which has the effect of
    # invoking x.current_user()
    @property
    def current_user(self):
        """Returns the logged in Spotify user, or None if unconnected."""
        if not hasattr(self, "_current_user"):
            self._current_user = None
            # find the user_id in a cookie
            user_id = parse_cookie(self.request.cookies.get("spotify_user"))
            if user_id:
                self._current_user = User.get_by_key_name(user_id)
        return self._current_user




### this will handle our home page
class HomeHandler(BaseHandler):
    # def pretty(obj):
    #     return json.dumps(obj, sort_keys=True, indent=2)

    def get_playlist_tracks(self, username, playlist_id, token):
        url = "https://api.spotify.com/v1/users/%s/playlists/%s/tracks" % (username, playlist_id)
        results = json.loads(spotifyurlfetch(url, token))
        tracks = results['items']

        # print(pretty(results))
        while results['next']:
            results = json.loads(spotifyurlfetch(results["next"], token))
            tracks.extend(results['items'])
        return tracks

    def getArtists(self, username, token):  # add a count of artists and tracks
        url = "https://api.spotify.com/v1/users/%s/playlists" % username
        ## in the future, should make this more robust so it checks if the access_token
        ## is still valid and retrieves a new one using refresh_token if not
        playlists = json.loads(spotifyurlfetch(url, token))
        # playlists = sp.user_playlists(username)
        artists = {}
        for playlist in playlists["items"]:
            if playlist['owner']['id'] == username:  # MAKE THIS AN OPTION TO CHOOSE FROM YOUR OWN MUSIC VERSUS ALL YOUR PLAYLISTS
                current_playlist = self.get_playlist_tracks(playlist['owner']['id'], playlist["id"], token=token)
                for track in current_playlist:
                    if track["track"]["artists"][0]["name"] in artists:
                        artists[track["track"]["artists"][0]["name"]].append(track["track"])
                    else:
                        artists[track["track"]["artists"][0]["name"]] = [track["track"]]
        return artists

    # running this from a local server, it stopped itself for a minute by itself
    # add a tracker of how many locations a user has
    # only has locations and artists, maybe make it so it has artist dictionaries
    # somehow make this action deferred will not run in GAE because of time
    def getLocations(self, artists):
        locations = {}
        for artist in artists.keys():
                try:
                    search = mb.search_artists(query=artist)
                except Exception:
                    pass
                try:
                    location = search["artist-list"][0]["begin-area"]["name"]
                except Exception:
                    try:
                        location = search["artist-list"][0]["area"]["name"]
                    except Exception:
                        location = "unknown"

                if location in locations.keys():
                    locations[location].append(artist)
                else:
                    locations[location] = [artist]
        return locations

    def do_geocode(self, location, geolocator):
        try:
            print("good")
            return geolocator.geocode(location)
        except GeocoderTimedOut:
            print("bad")
            time.sleep(2)
            return do_geocode(location)

    #fix so that it is storing data
    def getCoordinates(self, locations):
        coords = {}
        geolocator = Nominatim(user_agent="trepaolini@gmail.com")
        for location in locations:
            coord = self.do_geocode(location, geolocator)
            coords[location] = [coord.latitude, coord.longitude]

        return coords

    def get(self):
        template = JINJA_ENVIRONMENT.get_template('complete.html')
        
        # check if they are logged in
        user = self.current_user
        tvals = {'current_user': user}
        #sp = getSpotipyUser(user)
        #sp = spotipy.Spotify(auth=user.access_token)
        if user != None:
            artists = self.getArtists(username=self.current_user.uid, token=self.current_user.access_token)
            print(artists.keys())
            locations = self.getLocations(artists)


            print(locations.keys())
            coords = self.getCoordinates(locations)
            print(coords.keys())
            print(coords.values())
            tvals.update({ "locations" : locations, "artists" : artists, "coords" : coords})

        self.response.write(template.render(tvals))
        # if user != None:
        #     ## if so, get their playlists
        #     url = "https://api.spotify.com/v1/users/%s/playlists"%user.uid
        #     ## in the future, should make this more robust so it checks if the access_token
        #     ## is still valid and retrieves a new one using refresh_token if not
        #     response = json.loads(spotifyurlfetch(url,user.access_token))
        #     tvals["playlists"]=response["items"]





### this handler will handle our authorization requests 
class LoginHandler(BaseHandler):
    def get(self):
        # after  login; redirected here      
        # did we get a successful login back?
        args = {}
        args['client_id']= CLIENT_ID
        
        verification_code = self.request.get("code")
        if verification_code:
            # if so, we will use code to get the access_token from Spotify
            # This corresponds to STEP 4 in https://developer.spotify.com/web-api/authorization-guide/
                
            args["client_secret"] = CLIENT_SECRET
            args["grant_type"] = GRANT_TYPE
            args["code"] = verification_code                # the code we got back from Spotify
            args['redirect_uri']=self.request.path_url      # the current page
            
            # We need to make a post request, according to the documentation 
            
            #headers = {'content-type': 'application/x-www-form-urlencoded'}
            url = "https://accounts.spotify.com/api/token"
            response = urlfetch.fetch(url, method=urlfetch.POST, payload=urllib.urlencode(args))
            response_dict = json.loads(response.content)
            logging.info(response_dict["access_token"])
            access_token = response_dict["access_token"]
            refresh_token = response_dict["refresh_token"]

            # Download the user profile. Save profile and access_token
            # in Datastore; we'll need the access_token later
            
            ## the user profile is at https://api.spotify.com/v1/me
            profile = json.loads(spotifyurlfetch('https://api.spotify.com/v1/me',access_token))
            logging.info(profile)
           
            user = User(key_name=str(profile["id"]), uid=str(profile["id"]),
                        displayname=str(profile["display_name"]), access_token=access_token,
                        profile_url=profile["external_urls"]["spotify"], api_url=profile["href"], refresh_token=refresh_token)
            # if profile.get('images') is not None:
            #     user.img = profile["images"][0]["url"]
            user.put()
            
            ## set a cookie so we can find the user later
            set_cookie(self.response, "spotify_user", str(user.uid), expires=time.time() + 30 * 86400)
            
            ## okay, all done, send them back to the App's home page
            self.redirect("/complete")
        else:
            # not logged in yet-- send the user to Spotify to do that
            # This corresponds to STEP 1 in https://developer.spotify.com/web-api/authorization-guide/
            
            args['redirect_uri']=self.request.path_url
            args['response_type']="code"
            #ask for the necessary permissions - see details at https://developer.spotify.com/web-api/using-scopes/
            args['scope']="user-library-modify playlist-modify-private playlist-modify-public playlist-read-collaborative user-read-email"
            
            url = "https://accounts.spotify.com/authorize?" + urllib.urlencode(args)
            logging.info(url)
            self.redirect(url)


## this handler logs the user out by making the cookie expire
class LogoutHandler(BaseHandler):
    def get(self):
        set_cookie(self.response, "spotify_user", "", expires=time.time() - 86400)
        self.redirect("/complete")


application = webapp2.WSGIApplication([\
    ("/complete", HomeHandler),
    ("/auth/login", LoginHandler),
    ("/auth/logout", LogoutHandler)
], debug=True)