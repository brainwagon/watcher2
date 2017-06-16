#!/usr/bin/env python
#               __      __           ___ 
#  _    _____ _/ /_____/ /  ___ ____|_  |
# | |/|/ / _ `/ __/ __/ _ \/ -_) __/ __/ 
# |__,__/\_,_/\__/\__/_//_/\__/_/ /____/ 
#                                        
# A motion activated camera for the Raspberry Pi, written after reading 
# some of the documentation for the Python "picamera" module, and further
# enhanced by some ideas from spikedrba's "pmd" script, as seen on 
# https://gist.github.com/spikedrba/aecb82d8b51e991dbd01
#
# 

import sys
import os
import io
import subprocess
import logging
import logging.handlers
import time
import numpy as np
import picamera
import picamera.array
import platform
from pkg_resources import require
import datetime as dt

#
# Create a reasonable size log
#
logger = logging.getLogger("cam")
logger.setLevel(logging.DEBUG)
fh = logging.handlers.RotatingFileHandler("watcher2.log", mode='a', 
	maxBytes=(128*1024), backupCount=5, delay=0)
fh.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
logger.addHandler(fh)

logger.info('------------------------------------------------------------')
logger.info('Starting...')
logger.info(platform.platform())
logger.info("Using picamera version %s" % require('picamera')[0].version)
logger.info('------------------------------------------------------------')

class Queue:
    def __init__(self, maxsize):
        self.in_stack = []
        self.out_stack = []
        self.maxsize = maxsize
    def clear(self):
        self.in_stack = []
        self.out_stack = []
    def push(self, obj):
 	while self.len() >= self.maxsize:
	    self.pop()
        self.in_stack.append(obj)
    def pop(self):
        if not self.out_stack:
            while self.in_stack:
                self.out_stack.append(self.in_stack.pop())
        return self.out_stack.pop()
    def len(self):
        return len(self.in_stack) + len(self.out_stack)
    def isempty(self):
        return self.len() == 0
    def sum(self):
        return sum(self.in_stack) + sum(self.out_stack)
    def avg(self):
        return self.sum() / self.len()
    #if the queue is empty, these will toss an error.
    def max(self):
        return max(self.in_stack + self.out_stack)
    def min(self):
	return min(self.in_stack + self.out_stack)

dq = Queue(125)
sq = Queue(125)

motion_detected = False

class MyMotionDetector(picamera.array.PiMotionAnalysis):
   def analyse(self, a):
	global camera, sq, dq, motion_detected

	# compute image statistics...
	d = np.sqrt(np.square(a['x'].astype(np.float)) +
		    np.square(a['y'].astype(np.float))).clip(0, 255)
	d = d[d>0]
	s = np.sum(a['sad'].astype(np.int))

	dq.push(float(d.shape[0]))
	sq.push(float(s))

	motion_detected = dq.max() > 60


	# set the state variables that the main loop will read...

	# and now update the onscreen annotation...
	load = "%4.2f %4.2f %4.2f" % os.getloadavg()
	t = dt.datetime.now().strftime("braincam %x %X")
	#camera.annotate_text = t + "\n" + load
	camera.annotate_text = t

def write_video(stream, fname):
    # Write the entire content of the circular buffer to disk. No need to
    # lock the stream here as we're definitely not writing to it
    # simultaneously
    with io.open(fname, 'wb') as output:
        for frame in stream.frames:
            if frame.frame_type == picamera.PiVideoFrameType.sps_header:
                stream.seek(frame.position)
                break
        while True:
            buf = stream.read1()
            if not buf:
                break
            output.write(buf)
    # Wipe the circular stream once we're done
    stream.seek(0)
    stream.truncate()

def copy_remote(src):
    global logger
    dst = 'markv@10.0.0.4:capture'
    logger.info("Copying %s to %s" % (src, dst))
    try:
	rc = subprocess.check_call(['scp', '-p', '-q', src, dst])
	if rc != 0:
	    logger.error("Problem copying %s to %s" % (src, dst))
	else:
	    os.unlink(src)
	logger.info("Copy completed.")
    except:
	logger.info("Copy failed.")

camera = picamera.PiCamera() 

camera.framerate = 25
camera.start_preview()		# if you want to see what's happening

dir = "/var/tmp/capture"

# Make sure that dir exists....

if os.path.exists(dir):
    if not os.path.isdir(dir):
	logger.info("Unlinking %s to create a directory" % dir)
	os.unlink(dir)
	logger.info("Creating logging directory %s" % dir)
	os.makedirs(dir)
else:
    logger.info("Creating logging directory %s" % dir)
    os.makedirs(dir)

# Probably worth looking at...
# to pick a resolution https://picamera.readthedocs.org/en/latest/fov.html
camera.resolution = (1280, 720)
camera.vflip = True
camera.hflip = True
camera.annotate_background = True
camera.annotate_text_size = 40
camera.start_preview()

logger.info("Waiting for camera settings to settle.")
time.sleep(2)

# fix them for the recording...
camera.shutter_speed = camera.exposure_speed
camera.exposure_mode = 'night'
g = camera.awb_gains
camera.awb_mode = 'off'
camera.awb_gains = g

# Create a circular buffer to dump the high resolution data 
stream = picamera.PiCameraCircularIO(camera, seconds=5)

msize = (512,288)
#msize = (256,144)

camera.start_recording(stream, format='h264', splitter_port=1, intra_period=5)
camera.start_recording('/dev/null', format='h264', splitter_port=2, resize=msize,	
		motion_output=MyMotionDetector(camera, size=msize))
   
camera.wait_recording(5, splitter_port=1)

try:
    while True:
	camera.wait_recording(0.5)
	if motion_detected:
	    base = dt.datetime.now().strftime("wat-%H%M%S")
	    part1 = os.path.join(dir, base+"-A.h264")
	    part2 = os.path.join(dir, base+"-B.h264")
	    logger.info("Dumping %s %s to %s" % (base + "-A", base + "-B", dir))
	    camera.split_recording(part2, splitter_port=1)
	    write_video(stream, part1)
	    logger.info("Dump of %s completed." % (base + "-A"))
	    while motion_detected:
		camera.wait_recording(0.5)
	    camera.split_recording(stream, splitter_port=1)
	    logger.info("Dump of of %s completed." % (base + "-B"))
	    # copy things remotely... (asynchronously)
	    copy_remote(part1)
	    copy_remote(part2)
finally:
    camera.stop_recording(splitter_port=1)
    camera.stop_recording(splitter_port=2)

logger.info('------------------------------------------------------------')
logger.info('Finishing...')
logger.info('------------------------------------------------------------')
