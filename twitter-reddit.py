import praw
import prawcore
import configparser
import logging
import tweepy
import re
import yaml
import pickle
import inspect
from datetime import datetime
import psycopg2
import time
import os
import sys
import notificationManager

script_dir = os.path.split(os.path.realpath(__file__))[0]  # get where the script is
logging.basicConfig(filename=script_dir+'/logs/twitterBot.log',level=logging.INFO, format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')
currentSubreddit = "" # used for sending warnings
#
# TWITTER WIDGET V3
# by /u/chaos_a
# a twitter feed for subreddits

class WarningHandler:
    """Count the number of warnings thrown"""
    def __init__(self):
        self.counter=0

    def Warn(self, message):
        self.counter+=1
        logging.warning(message)


def Main():
    global Warning
    logging.info("--------Starting Twitter Bot--------")
    botconfig = configparser.ConfigParser()
    botconfig.read(script_dir + "/botconfig.ini")
    cycleCounter = 1
    testMode = False
    Warning = WarningHandler()
    for arg in sys.argv:
        if arg in ("-t", "-test"):
            testMode = True
            break
    while True: # run this part forever
        try:
            cycleCounter+=1
            if 480 % cycleCounter == 0: # every 480 rounds, or approx 4 days
                with open(script_dir + "/logs/twitterBot.log", 'w') as f:  # delete the old logs
                    f.write(f"Reset log file on UTC {datetime.utcnow()}")
            #Warning.Warn = WarningCounter(Warning.Warn) # setup warning tracker
            # twitter auth
            Warning.counter = 0
            auth = tweepy.OAuthHandler(botconfig.get("twitter", "APIKey"), botconfig.get("twitter", "APISecret"))
            auth.set_access_token(botconfig.get("twitter", "AccessToken"), botconfig.get("twitter", "TokenSecret"))
            global currentSubreddit
            global tApi
            tApi = tweepy.API(auth)
            reddit = redditlogin(botconfig)
            global conn2
            conn2 = dbConnect(botconfig)
            cur = conn2.cursor()
            # setup test mode
            if testMode:
                print("Test mode")
                cur.execute("SELECT * FROM subreddits_testing")
            else: # not in test mode, use real database
                cur.execute("SELECT * FROM subreddits")
            results = cur.fetchall()
            for subredditdata in results: # go through every subreddit
                currentSubreddit = subredditdata[0] # used for warnings only
                logging.info("Checking tweets for subreddit %s" % subredditdata[0])
                if subredditdata[1]: # bot is enabled for this subreddit via database
                    subreddit = reddit.subreddit(subredditdata[0]) # set the subreddit
                    try:
                        wiki = subreddit.wiki['twittercfg'].content_md # get the config wiki page
                        config = yaml.load(wiki, Loader=yaml.FullLoader) # load it
                        if config: # if the file actually works
                            if checkCfg(subreddit, config): # validate that everything in the config is correct
                                if config.get('enabled', False): # bot is enabled via config
                                    try:
                                        getTweets(subreddit, config, subredditdata) # get new tweets
                                    except Exception as e:
                                        Warning.Warn(f"{e.__class__.__name__}: An error occurred while checking tweets on subreddit {subredditdata[0]}: {e}")
                                else:
                                    logging.info(f"Subreddit {subreddit.display_name} is disabled")
                            else:
                                Warning.Warn("Bad config file on subreddit %s" % subreddit.display_name)
                        else:
                            Warning.Warn("BROKEN CONFIG FILE on subreddit %s" % subreddit.display_name)

                    except prawcore.exceptions.NotFound:
                        try:
                            subreddit.wiki.create(name='twittercfg', content='---  \nenabled: false  \nmode: user')
                            logging.info("Created wiki page on subreddit %s" % subreddit.display_name)
                        except prawcore.exceptions.NotFound: # occurs when lacking permissions to create wiki page
                            Warning.Warn("Tried to create wiki page but failed. Bot probably lacks permission. Subreddit: %s" % subreddit
                                            .display_name)
                        except Exception as e:
                            Warning.Warn(f"{e.__class__.__name__}: Something else happened while trying to create the wiki page? This should never occur. Exception: {e}")

                    except Exception as e:
                        Warning.Warn(f"{e.__class__.__name__}: Possibly got removed, but did not update database. Or this is a config error. Exception: {e}")
                        sendWarning(subreddit, "An exception occurred while loading the config:\n\n %s" % e)
                else:
                    logging.info("Subreddit %s is disabled" % subredditdata[0])

            # check to see how many errors occurred, then send out the appropriate notifications
            logging.info(f"Warnings thrown during cycle: {Warning.counter}")
            LoggingChannelID = botconfig.get("notification", "SendChannelID")
            if Warning.counter > len(results)/2: # check if most of the subreddits are throwing errors
                res = notificationManager.sendStatus(f"Too many warnings are being thrown! {Warning.counter}", True, LoggingChannelID)
                if res:
                    Warning.Warn("notificationManager returned an error: "+res)
            else:
                res = notificationManager.sendStatus(f"Current Cycle: {cycleCounter} \nWarnings thrown during cycle: {Warning.counter}", False, LoggingChannelID)
                if res:
                    Warning.Warn("notificationManager returned an error: "+res)

            logging.info("Done with tweets, sleeping for 5 mins")
            time.sleep(300)
        except prawcore.ServerError as e:
            logging.error(f"Server error: {e}")
            time.sleep(200)
        except Exception as e:
            logging.error(f"Exception: {e}")
            time.sleep(200)

def getTweets(subreddit, config, subredditdata):
    try:
        print("here0"+subreddit.display_name)
        global isNew
        isNew = False # informs late code that tweets are either new or old
        if 'mode' in config:
            print("here1")
            mode = config.get('mode') # get current mode
        else:
            print("here3")
            sendWarning(subreddit, "Config Error: Missing mode type (list/user)")
            return
        print("here4")
        count = config.get('count', 7) # get number of tweets to display
        print("here4.5")
        if count > 15: # enforce limit
            count = 15
            print("here6")
        print("here5")
        if mode == 'user': # get tweets from a single user
            print("here7")
            user = config.get('screen_name')
            LatestTweet = tApi.user_timeline(screen_name=user, count=1, tweet_mode='extended', include_entities=True)  # get first tweets id number
            Tweets = checkTweets(LatestTweet, subredditdata) # check LatestTweet is latest, if it is it just returns stored tweets, otherwise we need to get new tweets here
            if not Tweets: # returned as false, need to get new tweets
                isNew = True
                Tweets = tApi.user_timeline(screen_name=user, count=count, tweet_mode='extended',include_entities=True)  # gathers new tweets
                storeNewTweets(Tweets, subredditdata) # store's the new tweets away in the .data file
            if checkLastUpdate(subredditdata[0], Tweets[0].created_at):
                logging.info("Updating widget")
                MakeMarkupUser(Tweets, subreddit, config, mode)  # use the user markup function
        elif mode == 'list': # get tweets by many users via a list
            # note: yes this is a mess and really this whole file needs to re-written from scratch
            # some of the code here had to be duplicated to support list id's as the old list name method broke
            list = config.get("list")
            if isinstance(list, str): # if not number
                logging.info("Using list name mode for list id")
                LatestTweet = tApi.list_timeline(owner_screen_name=config['owner'], slug=list.lower(), count=1, tweet_mode='extended',include_entities=True)  # get first tweets id number
                Tweets = checkTweets(LatestTweet, subredditdata)
                if not Tweets: # returned as false, get new tweets
                    isNew = True
                    Tweets = tApi.list_timeline(owner_screen_name=config['owner'], slug=list, count=count, tweet_mode='extended',include_entities=True) # get new tweets
                    storeNewTweets(Tweets, subredditdata) # store's the tweets away in the .data file
            elif isinstance(list, int): # number mode
                logging.info("Using number mode for list id")
                list_id = config.get("list")
                LatestTweet = tApi.list_timeline(list_id=list_id, count=1,tweet_mode='extended',include_entities=True)  # get first tweets id number
                Tweets = checkTweets(LatestTweet, subredditdata)
                if not Tweets:  # returned as false, get new tweets
                    isNew = True
                    Tweets = tApi.list_timeline(list_id=list_id, count=count,
                                                tweet_mode='extended', include_entities=True)  # get new tweets
                    storeNewTweets(Tweets, subredditdata)  # store's the tweets away in the .data file
            else:
                Warning.Warn("Unknown listid mode!")
                sendWarning(subreddit, "Unknown list_id mode, please message /r/tweet_widget if you see this.")
            if checkLastUpdate(subredditdata[0], Tweets[0].created_at):
                logging.info("Updating widget")
                MakeMarkupList(Tweets, subreddit, config, mode) # use the list markup function
    except tweepy.TweepError as e:
        Warning.Warn(f"{e.__class__.__name__}: An error occurred while gathering tweets: {e}")
        sendWarning(subreddit, f"Twitter related issue, check that list and accounts are publicly visible.")
        return
    except Exception as e:
        Warning.Warn(f"{e.__class__.__name__}: An error occurred while gathering tweets: {e}")
        sendWarning(subreddit, f"Unexpected Error. Full Error: {e}")
        return


def checkTweets(Tweets, subredditdata): # checks if the latest tweet is in the database, meaning that it is already in the widget
    # function also returns old tweets that are stored in /Data/"Subreddit".data files.
    # this is done this way to reduce the number of API calls, since we can easily store tweets and still update the timestamps
    global script_dir
    global conn
    try:
        if subredditdata[2] == Tweets[0].id_str: # id's do match
            with open("{}/Data/{}.data".format(script_dir,subredditdata[0]), mode="rb") as f: # read saved data
                data = pickle.load(f)
                logging.info("Stored tweet is latest, using data file instead of getting more tweets for subreddit %s" % subredditdata[0])
                return data # return stored tweets (becomes Tweets)
        else: # latest tweet does not match stored tweet, get new tweets
            print("Updating database?")
            cur = conn2.cursor()
            cur.execute("UPDATE subreddits SET latest={} WHERE subname='{}'".format(Tweets[0].id_str,subredditdata[0])) # update latest id number
            logging.info("Getting new tweets for subreddit %s" % subredditdata[0])
            return False # gather new tweets
    except Exception as e:
        if e == IndexError:
            Warning.Warn(f"{e.__class__.__name__}: Has user posted a tweet? subreddit: {subredditdata[0]}, error {e}")
        else:
            Warning.Warn("An error occurred while checking/gathering stored tweets: %s" %e)
        return False # gather new tweets anyways

def storeNewTweets(Tweets, subredditdata): # stores the new tweets so they can be used again
    logging.info("Storing tweets")
    global script_dir
    try:
        with open("{}/Data/{}.data".format(script_dir,subredditdata[0]), mode='wb') as f:
            pickle.dump(Tweets, f)
        logging.info("Successfully stored new tweets to .data file")
    except Exception as e:
        Warning.Warn(f"{e.__class__.__name__}: An error occurred while storing new tweets: {e}")
    # store timestamp
    global conn
    try:
        cur = conn2.cursor()
        cur.execute("UPDATE subreddits SET last_gather={} WHERE subname='{}'".format(datetime.utcnow().timestamp(), subredditdata[0]))
    except Exception as e:
        Warning.Warn(f"{e.__class__.__name__}: An error occurred while storing last_gather: {e}")

def getLastGatherTimestamp(subname): # returns last_gather datetime object
    try:
        cur = conn2.cursor()
        cur.execute("SELECT last_gather FROM subreddits WHERE subname='{}'".format(subname))
        res = cur.fetchone()
        return datetime.fromtimestamp(res[0])
    except Exception as e:
        Warning.Warn(f"{e.__class__.__name__}: An error occurred while getting last_gather: {e}")
        return

# this function confirms if this widget should be updated
# this is here so that it upload the widget only when needed. Spamming reddit widgets every 5 minutes for no reason causes glitches
def checkLastUpdate(subname, t_created_at):
    try:
        # calculate time difference
        time_diff = datetime.utcnow() - t_created_at  # current time minus tweet time, both are UTC
        seconds = time_diff.total_seconds()  # convert to seconds
        if 3930 < seconds: # latest tweet is older than one hour + 5 mins and 30s, this ensures that it will display 1 hour and not 59 mins
            cur = conn2.cursor()
            cur.execute("SELECT last_update FROM subreddits WHERE subname='{}'".format(subname))
            res = cur.fetchone()[0]
            if res is None: # on first run the data is null/none, next time it'll use a timestamp
                setLastUpdateTimestamp(subname)
                return True
            time_diff_res = datetime.utcnow() - datetime.fromtimestamp(res)  # tweet time - stored time
            seconds_res = time_diff_res.total_seconds()  # convert to seconds
            if 1800 < seconds_res: # check if half an hour has passed since the widget was last updated
                logging.info("Half an timer hour has passed, updating widget")
                return True
            else: # half an hour has NOT passed, so we don't need to bother with updating the widget
                logging.info("Waiting for half hour timer")
                return False
        else: # tweet is under an hour old, do update the widget
            return True
    except Exception as e:
        Warning.Warn(f"{e.__class__.__name__}: An error occurred while running checkLastUpdate: {e}")
        return

# sets last update time, this runs AFTER the widget has been uploaded
def setLastUpdateTimestamp(subname):
    logging.info("Setting latest update timestamp")
    try:
        cur = conn2.cursor()
        cur.execute("UPDATE subreddits SET last_update={} WHERE subname='{}'".format(datetime.utcnow().timestamp(), subname))
    except Exception as e:
        Warning.Warn(f"{e.__class__.__name__}: An error occurred while getting last_update: {e}")
        return

def genericItems(t, subreddit, config): # bunch of normally repeated code between MakeMarkupUser and MakeMarkupList
    try:
        hotlinkFormat = "https://www.twitter.com/{0}/status/{1}".format(t.user.screen_name, t.id)  # format a link to the tweet with username and tweet id
        timestampStr = convertTime(t.created_at) # tweet timestamp
        profileUrl = "https://www.twitter.com/"  # this + username gives a link to the users profile
        if hasattr(t, "retweeted_status"): # check if retweet, if so do retweet stuff
            try:
                hotlinkFormatRT = "https://www.twitter.com/{0}/status/{1}".format(t.retweeted_status.user.screen_name, t.retweeted_status.id)
                timestampStrRT = convertTime(t.retweeted_status.created_at) # get retweet timestamp
                tweet_text = tweetFormatting(t.retweeted_status, t.retweeted_status.full_text) # do tweet formatting on retweet
                tweet_text = "*🔁{} Retweeted*\n\n**[{} *@{}*]({}) *-* [*{}*]({})**  \n{}".format(t.user.name, t.retweeted_status.user.name, t.retweeted_status.user.screen_name, profileUrl+t.retweeted_status.user.screen_name.lower(), timestampStrRT, hotlinkFormatRT, tweet_text)
                fulltext = tweet_text.replace("\n","\n>>")  # double quotes so that it forms two blockquote elements
            except Exception as e:
                Warning.Warn(f"{e.__class__.__name__}: An error occurred while formatting a retweet: {e}")
                return
        else: # isn't a retweet, just normal stuff
            tweet_text = tweetFormatting(t, t.full_text) # do tweet formatting
            fulltext = tweet_text.replace("\n","\n>")  # add the '>' character for every new line so it doesn't break the quote

        if len(t.user.screen_name + t.user.name) > 36:
            screen_name = t.user.screen_name[0:33]  # username is too long, shorten it
        else:
            screen_name = t.user.screen_name  # normal
        return hotlinkFormat, timestampStr, profileUrl, fulltext, screen_name

    except Exception as e:
        Warning.Warn(f"{e.__class__.__name__}: An error occurred while formatting a tweet/retweet: {e}")

def MakeMarkupUser(Tweets, subreddit, config, mode): # twitter user mode
    try:
        markup = ("#{}\n".format(config.get('title', "Tweets"))) # custom title
        for t in Tweets:
            hotlinkFormat, timestampStr, profileUrl, fulltext, screen_name = genericItems(t, subreddit, config)
            # MARKUP NOTE: 2 hashes are used here to signal %%profile1%%
            markup += ("\n\n---\n##**[{} *@{}*]({})**   \n[*{}*]({}) \n>{}".format(t.user.name, screen_name, profileUrl+t.user.screen_name.lower(), timestampStr, hotlinkFormat,fulltext))
            if config.get('show_retweets', False): # add re-tweet info
                markup += ("\n\n>**{}** Retweets  **{}** Likes".format(t.retweet_count, t.favorite_count))
        else: # once markup is done
            insertMarkup(subreddit, markup, config, mode) # put it on the subreddit
    except Exception as e:
        Warning.Warn(f"{e.__class__.__name__}: An error occurred while making the markup on subreddit {subreddit.display_name}: {e}")

def MakeMarkupList(Tweets, subreddit, config, mode): # twitter list mode
    global timezone
    try:
        markup = ("#{}\n".format(config.get('title', 'Tweets'))) # custom title
        userhashes = {k.casefold(): v for k, v in config['users'].items()}  # make all dict items lowercase
        for i in userhashes: # here to deal with possible user shenanigans
            if userhashes[i] > 5: userhashes[i] = 5 # any number bigger than 5, set to 5
            elif userhashes[i] <= 0: userhashes[i] = 1 # same thing, but to 1
        # FORMATTING INFO: Userhashes (above) is used to calculate which header value is used (h2-h6)
        # the rest is css magic
        for t in Tweets:
            hotlinkFormat, timestampStr, profileUrl, fulltext, screen_name = genericItems(t, subreddit, config)
            markup += ("\n\n---\n{}**[{} *@{}*]({})**   \n[*{}*]({}) \n>{}".format(('#'*(userhashes[t.user.screen_name.lower()]+1)), t.user.name, screen_name, profileUrl+t.user.screen_name.lower(), timestampStr, hotlinkFormat, fulltext))
            if config.get('show_retweets', False): # add re-tweet info
                markup += ("\n\n>**{}** Retweets  **{}** Likes".format(t.retweet_count, t.favorite_count))
        else: # once markup is done
            insertMarkup(subreddit, markup, config, mode) # put it on the subreddit
    except KeyError as e:
        sendWarning(subreddit, "KeyError, check your profiles in the config! User: %s"%e)
        Warning.Warn("Invalid key data: %s" % e)
    except Exception as e:
        Warning.Warn(f"{e.__class__.__name__}: An error occurred while making the markup on subreddit {subreddit.display_name}: {e}")

def insertMarkup(subreddit, markup, config, mode): # places the markup into the widget
    try:
        if "view_more_url" in config: # custom view more button
            markup += ("\n\n**[View more tweets]({})**".format(config.get('view_more_url')))
        else: # default view more urls
            if mode == "user": # default to profile url
                markup += ("\n\n**[View more tweets](https://www.twitter.com/{})**".format(config.get('screen_name')))
            elif mode == "list": # default to list url (owner username/lists/listname)
                list = config.get('list')
                if isinstance(list, str):
                    markup += ("\n\n**[View more tweets](https://www.twitter.com/{}/lists/{})**".format(config.get('owner'), config.get('list').lower()))
                elif isinstance(list, int):
                    markup += ("\n\n**[View more tweets](https://www.twitter.com/i/lists/{})**".format(list))
                else:
                    logging.warning(f"Unknown list type {list}")
        markup+= "\n\n~~" # open code area
        markup+= "Widget last updated: {}".format(datetime.utcnow().strftime("%-d %b at %-I:%M %p")+" (UTC)  \n")
        markup+= "Last retrieved tweets: {}".format(getLastGatherTimestamp(subreddit.display_name.lower()).strftime("%-d %b at %-I:%M %p")+" (UTC)  \n")
        if config.get('show_ad', True): # place ad into widget
            markup+= "[/r/Tweet_widget](https://www.reddit.com/r/tweet_widget)"
        markup += "~~" # close code area
    except Exception as e:
        Warning.Warn(f"{e.__class__.__name__}: An error occurred while doing end of widget text: {e}")
    try:
        widgets = subreddit.widgets.sidebar  # get all widgets
        for item in widgets:
            if item.shortName.lower() == 'twitterfeed': # find the feed widget
                item.mod.update(shortname="twitterfeed", text=markup) # update the widget
                setLastUpdateTimestamp(subreddit.display_name.lower())
                logging.info("Updated the text for /r/%s" % subreddit.display_name)
                return # we're done here
    except Exception as e:
        Warning.Warn(f"{e.__class__.__name__}: An error occurred while dealing with widgets on subreddit {subreddit.display_name}: {e}")

def convertTime(t_created_at):
    time_diff = datetime.utcnow() - t_created_at # current time minus tweet time, both are UTC
    seconds = time_diff.total_seconds() # convert to seconds
    if seconds < 60:
        timeStr = "Just Now"
    elif 60 < seconds < 3600: # younger than 1 hour, show mins
        timeStr = str(int((seconds % 3600) // 60)) + "m"
    elif 3600 < seconds < 86400: # older than 1 hour, younger than 1 day, show hours
        timeStr = str(int(seconds // 3600)) + "h"
    else: # older than 1 day
        timeStr = t_created_at.strftime("%b %-d, %Y")  # timestamp
    return timeStr.strip() # removes unwanted spaces

def escapeChars(fulltext): # escapes existing characters in a tweet to stop reddit from formatting on them
    redditChars = ["[", "]", "#", "*", ">", "^", "<", "~", "_", "`", "|", "-"]
    for i in redditChars:
        if i in fulltext: # if i is one of the characters used by reddit for formatting
            fulltext = fulltext.replace(i, "\\"+i) # escape the character
    else:
        return fulltext

def tweetFormatting(t, tweet_text): # does a bunch of formatting to various parts of the tweet
    tweet_text = escapeChars(tweet_text) # run the escape characters function first
    json = t._json
    linkformat = "[{}]({})"
    try: # replace links with correctly formatted text and full urls rather than t.co
        if json['entities'].get('urls') is not None:
            for i in t._json['entities']['urls']:
                fixedUrl = re.sub(r"https?://", '', i['expanded_url']).strip("/") # remove https://, http:// and trailing / so the link looks good
                tweet_text = tweet_text.replace(i['url'], linkformat.format(fixedUrl, i['expanded_url'])) # replace the t.co item with the fixedUrl (display only) and full url for the link
        if json['entities'].get('media') is not None:
            for i in t._json['entities']['media']:
                if i.get('type') == 'photo': # make the image link direct to the photo
                    tweet_text = tweet_text.replace(i['url'], linkformat.format(i['display_url'], i['media_url_https'])) # replace the t.co item with the pics.twitter.com url (display only) and direct image link
                else: # links directly to the tweet/media item
                    tweet_text = tweet_text.replace(i['url'], linkformat.format(i['display_url'], i['expanded_url'])) # same as above, but links to the tweet rather than directly to content
    except Exception as e:
        Warning.Warn(f"{e.__class__.__name__}: An error occurred while formatting {e}")

    # find @ symbols and link to the tagged users profile
    twitterprofileUrl = "*[@{}](https://www.twitter.com/{})*"
    res = re.findall('@(\w+)', tweet_text)
    if res:
        for i in set(res): # using set here otherwise replace will act on duplicates multiple times
             tweet_text = tweet_text.replace('@'+i, twitterprofileUrl.format(i, i)) # replaces with link
    # find # symbols and link them
    hashtagUrl = "*[\#{}](https://www.twitter.com/search?q=%23{})*"
    res = re.findall("#(\w+)", tweet_text)
    if res:
        for i in set(res): # using set here otherwise replace will act on duplicates multiple times
            tweet_text = tweet_text.replace('\#' + i, hashtagUrl.format(i, i))  # replaces with link
    return tweet_text # we are done here, return the edited tweet text


def checkCfg(subreddit, config): # False = Failed checks, True = Pass, continue code
    if 'enabled' not in config:
        sendWarning(subreddit, "Config Missing: enabled")
        return False # missing key data
    if 'mode' not in config:
        sendWarning(subreddit, "Config Missing: mode")
        return False
    if config['mode'] == 'list':
        if 'list' not in config:
            sendWarning(subreddit, "Config Missing: List name is required for list mode")
            return False
        if isinstance(config.get("list"), str):
            if 'owner' not in config:
                sendWarning(subreddit, "Config Missing: Owner name is required for list mode ONLY WHEN using the list name")
                return False
        if 'users' not in config:
            sendWarning(subreddit, "Config Missing: Username's (users) are required for list mode")
            return False
        try:
            config['users'].items()
        except AttributeError as e: # added due to a config file lacking indents
            Warning.Warn(f"{e.__class__.__name__}: Attribute error thrown. Bad config file.")
            sendWarning(subreddit, "Config Error: Missing or incorrect formatting on userlist. Check indentation/config formatting.")
            return False
        except Exception as e: # handle case of another error happening here
            Warning.Warn(f"{e.__class__.__name__}: Another. Likely bad config file.")
            return True # might cause issues?

    elif config['mode'] == 'user':
        if 'screen_name' not in config:
            sendWarning(subreddit, "Config Missing: Users screen name is required for user mode")
            return False
    else:
        sendWarning(subreddit, "Config Error: Mode is not set to a valid value")
        return False
    return True # if the code get's here nothing went wrong

def sendWarning(subreddit, message):
    try:
        endMsg = "\n\n*"
        endMsg+="[/r/Tweet_widget](https://www.reddit.com/r/tweet_widget)"
        endMsg+= "*"
        message = message.replace("\n", "\n  ")
        widgets = subreddit.widgets.sidebar  # get all widgets
        for item in widgets:
            if item.shortName.lower() == 'twitterfeed':  # find the feed widget
                item.mod.update(shortname="twitterfeed", text="An error occurred with tweet_widget bot:\n"+message+"\n\n"+endMsg)  # update the widget
                logging.info("An error message ({}) was posted to /r/{}".format(message, subreddit.display_name))
                return  # we're done here
    except Exception as e:
        logging.error(f"An error occurred while sending a warning: {e}")

def dbConnect(botconfig):
    # DB Connection
    dbName = botconfig.get("database", "dbName")
    dbPasswrd = botconfig.get("database", "dbPassword")
    dbUser = botconfig.get("database", "dbUsername")
    dbHost = botconfig.get("database", "dbHost")
    # INFO: database is setup is: subreddits(subname varchar, enabled bool DEFAULT True, latest varchar)
    try:
        global conn2
        print(dbName, dbUser, dbHost, dbPasswrd)
        conn2 = psycopg2.connect( # connect
            "dbname='{0}' user='{1}' host='{2}' password='{3}'".format(
                dbName, dbUser, dbHost, dbPasswrd
            )
        )
        conn2.autocommit = True
        return conn2
    except Exception as e: # could not connect
        Warning.Warn(f"{e.__class__.__name__}: Cannot connect to database")
        time.sleep(120)

def redditlogin(botconfig):
    # reddit login
    try:
        r = praw.Reddit(client_id=botconfig.get("reddit", "clientID"),
                        client_secret=botconfig.get("reddit", "clientSecret"),
                        password=botconfig.get("reddit", "password"),
                        user_agent=botconfig.get("reddit", "useragent"),
                        username=botconfig.get("reddit", "username"))
        me = r.user.me()
        return r # return reddit instance
    except Exception as e: # reddit is down
        Warning.Warn(f"{e.__class__.__name__}: Reddit/PRAW Issue, site may be down")
        time.sleep(120)

if __name__ == "__main__":
    Main()
