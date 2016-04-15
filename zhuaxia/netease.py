# -*- coding:utf-8 -*-
import time
import re
import requests
import log, config, util
import json
import md5
import os
from os import path
import downloader
from obj import Song, Handler

if config.LANG.upper() == 'CN':
    import i18n.msg_cn as msg
else:
    import i18n.msg_en as msg

LOG = log.get_logger("zxLogger")

#163 music api url
url_163="http://music.163.com"
#url_mp3="http://m1.music.126.net/%s/%s.mp3" #not valid any longer
url_album="http://music.163.com/api/album/%s/"
url_song="http://music.163.com/api/song/detail/?id=%s&ids=[%s]"
url_playlist="http://music.163.com/api/playlist/detail?id=%s"
url_artist_top_song = "http://music.163.com/api/artist/%s"
url_lyric = "http://music.163.com/api/song/lyric?id=%s&lv=1"
url_mp3_post = 'http://music.163.com/weapi/song/enhance/player/url?csrf_token='

#agent string for http request header
AGENT= 'Mozilla/5.0 (X11; Linux i686) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/28.0.1500.95 Safari/537.36'

#this block is kind of magical secret.....No idea why the keys, modulus have those values ( for building the post request parameters. The encryption logic was take from https://github.com/Catofes/musicbox/blob/new_api/NEMbox/api.py)
modulus = '00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7'
nonce = '0CoJUm6Qyw8W8jud'
pubKey = '010001'



class NeteaseSong(Song):
    """
    163 Song class, if song_json was given,
    Song.post_set() needs to be called for post-setting
    abs_path, filename, etc.
    url example: http://music.163.com/song?id=209235
    """

    def __init__(self,m163,url=None, song_json=None):
        Song.__init__(self)
        self.song_type=2
        self.handler = m163
        # self.group_dir = None
        # self.lyric_text = ''

        if url:
            self.url = url
            self.song_id = re.search(r'(?<=/song\?id=)\d+', url).group(0)

            LOG.debug(msg.head_163 + msg.fmt_init_song % self.song_id)
            j = self.handler.read_link(url_song % (self.song_id,self.song_id)).json()['songs'][0]
            self.init_by_json(j)
            LOG.debug(msg.head_163 + msg.fmt_init_song_ok % self.song_id)
            #set filename, abs_path etc.
            self.post_set()

        elif song_json:
            self.init_by_json(song_json)


    def init_by_json(self,js):
        #song_id
        self.song_id = js['id']
        # meta
        if self.handler.need_detail:
            self.json = js
        #name
        self.song_name = util.decode_html(js['name'])
        LOG.debug("parsing song %s ...."%self.song_name)

        # artist_name
        self.artist_name = js['artists'][0]['name']
        # album id, name
        self.album_name = util.decode_html(js['album']['name'])
        self.album_id = js['album']['id']
        #track no
        if 'position' in js and js['position']:
            self.track_no = js['position']
        elif 'no' in js and js['no']:
            self.track_no = js['no']

        # download link
        dfsId = ''
        bitrate = 0
        if self.handler.is_hq and js['hMusic']:
            dfsId = js['hMusic']['dfsId']
            quality = 'HD'
            bitrate = js['hMusic']['bitrate']
        elif js['mMusic']:
            dfsId = js['mMusic']['dfsId']
            quality = 'MD'
            bitrate = js['mMusic']['bitrate']
        elif js['lMusic']:
            LOG.warning(msg.head_163 + msg.fmt_quality_fallback %self.song_name)
            dfsId = js['lMusic']['dfsId']
            quality = 'LD'
            bitrate = js['lMusic']['bitrate']
        if dfsId:
            # self.dl_link = url_mp3 % (self.handler.encrypt_dfsId(dfsId), dfsId)
            self.dl_link = self.handler.get_mp3_dl_link(self.song_id, bitrate)
        else:
            LOG.warning(msg.head_163 + msg.fmt_err_song_parse %self.song_name)

        #used only for album/collection etc. create a dir to group all songs
        #if it is needed, it should be set by the caller
        self.group_dir = None


class NeteaseAlbum(object):
    """The netease album object"""

    def __init__(self, m163, url):
        """url example: http://music.163.com/album?id=2646379"""

        self.handler=m163
        self.url = url
        self.album_id = re.search(r'(?<=/album\?id=)\d+', self.url).group(0)
        LOG.debug(msg.head_163 + msg.fmt_init_album % self.album_id)
        self.year = None
        self.track=None
        self.songs = [] # list of Song
        self.init_album()

    def init_album(self):
        #album json
        js = self.handler.read_link(url_album % self.album_id).json()['album']
        #name
        self.album_name = util.decode_html(js['name'])
        #album logo
        self.logo = js['picUrl']
        # artist_name
        self.artist_name = js['artists'][0]['name']
        #handle songs
        for jsong in js['songs']:
            song = NeteaseSong(self.handler, song_json=jsong)
            song.group_dir = self.artist_name + u'_' + self.album_name
            song.post_set()
            self.songs.append(song)

        d = path.dirname(self.songs[-1].abs_path)
        #creating the dir
        LOG.debug(msg.head_163 + msg.fmt_create_album_dir % d)
        util.create_dir(d)

        #download album logo images
        LOG.debug(msg.head_163 + msg.fmt_dl_album_cover % self.album_name)
        downloader.download_url(self.logo, path.join(d,'cover.' +self.logo.split('.')[-1]))

class NeteasePlayList(object):
    """The netease playlist object"""
    def __init__(self, m163, url):
        self.url = url
        self.handler = m163
        #user id in url
        self.playlist_id = re.search(r'(?<=/playlist\?id=)\d+', self.url).group(0)
        self.songs = []
        self.init_playlist()

    def init_playlist(self):
        j = self.handler.read_link(url_playlist % (self.playlist_id) ).json()['result']
        self.playlist_name = j['name']
        for jsong in j['tracks']:
            song = NeteaseSong(self.handler, song_json=jsong)
            #rewrite filename, make it different
            song.group_dir = self.playlist_name
            song.post_set()
            self.songs.append(song)
        if len(self.songs):
            #creating the dir
            util.create_dir(path.dirname(self.songs[-1].abs_path))

class NeteaseTopSong(object):
    """download top songs of given artist"""
    def __init__(self, m163, url):
        self.url = url
        self.handler = m163
        #artist id
        self.artist_id = re.search(r'(?<=/artist\?id=)\d+', self.url).group(0)
        self.artist_name = ""
        self.songs = []
        self.init_topsong()

    def init_topsong(self):
        j = self.handler.read_link(url_artist_top_song % (self.artist_id)).json()
        self.artist_name = j['artist']['name']
        for jsong in j['hotSongs']:
            song = NeteaseSong(self.handler, song_json=jsong)
            song.group_dir = self.artist_name + '_TopSongs'
            song.post_set()
            self.songs.append(song)
            #check config for top X
            if config.DOWNLOAD_TOP_SONG>0 and len(self.songs) >= config.DOWNLOAD_TOP_SONG:
                break

        if len(self.songs):
            #creating the dir
            util.create_dir(path.dirname(self.songs[-1].abs_path))

class Netease(Handler):

    """
    netease object
    option is the user given options and other data @see __init__
    """
    def __init__(self, option):
        Handler.__init__(self,option.proxies)
        self.is_hq = option.is_hq
        self.need_detail = option.need_detail
        self.dl_lyric = option.dl_lyric
        #headers
        self.HEADERS = {'User-Agent':AGENT}
        self.HEADERS['Referer'] = url_163
        self.HEADERS['Cookie'] = 'appver=1.7.3'

    def read_link(self, link):

        retVal = None
        requests_proxy = {}
        if config.CHINA_PROXY_HTTP:
            requests_proxy = { 'http':config.CHINA_PROXY_HTTP}
        if self.need_proxy_pool:
            requests_proxy = {'http':self.proxies.get_proxy()}

            while True:
                try:
                    retVal =  requests.get(link, headers=self.HEADERS, proxies=requests_proxy)
                    break
                except requests.exceptions.ConnectionError:
                    LOG.debug('invalid proxy detected, removing from pool')
                    self.proxies.del_proxy(requests_proxy['http'])
                    if self.proxies:
                        requests_proxy['http'] = self.proxies.get_proxy()
                    else:
                        LOG.debug('proxy pool is empty')
                        raise
                        break
        else:
            retVal =  requests.get(link, headers=self.HEADERS, proxies=requests_proxy)
        return retVal

    def encrypt_dfsId(self,dfsId):
        byte1 = bytearray('3go8&$8*3*3h0k(2)2')
        byte2 = bytearray(str(dfsId))
        byte1_len = len(byte1)
        for i in xrange(len(byte2)):
            byte2[i] = byte2[i]^byte1[i%byte1_len]
        m = md5.new()
        m.update(byte2)
        result = m.digest().encode('base64')[:-1]
        result = result.replace('/', '_')
        result = result.replace('+', '-')
        return result

    def createSecretKey(self, size):
        return (''.join(map(lambda xx: (hex(ord(xx))[2:]), os.urandom(size))))[0:16]

    def encrypt_post_param(self,req_dict):
        text = json.dumps(req_dict)
        secKey = self.createSecretKey(16)
        encText = util.aes_encrypt(util.aes_encrypt(text, nonce), secKey)
        encSecKey = util.rsa_encrypt(secKey, pubKey, modulus)
        result = {
            'params': encText,
            'encSecKey': encSecKey
        }
        return result

    def get_mp3_dl_link(self, song_id,  bitrate):
        req = {
                "ids": [song_id],
                "br": bitrate,
                "csrf_token": ""
            }
        page = requests.post(url_mp3_post, data=self.encrypt_post_param(req), headers=self.HEADERS, timeout=30)
        result = page.json()["data"][0]["url"]

        #the redirect.....
        if result:

            r = self.read_link(result)
            if r.history:
                return  r.history[0].headers['Location']

        return result

