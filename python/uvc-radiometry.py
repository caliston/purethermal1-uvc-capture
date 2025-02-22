#!/usr/bin/env python
# -*- coding: utf-8 -*-

from uvctypes import *
import time
import datetime
import cv2
import numpy as np
try:
  from queue import Queue
except ImportError:
  from Queue import Queue
import platform
import sys
import argparse
import math

BUF_SIZE = 2
q = Queue(BUF_SIZE)

def py_frame_callback(frame, userptr):

  array_pointer = cast(frame.contents.data, POINTER(c_uint16 * (frame.contents.width * frame.contents.height)))
  data = np.frombuffer(
    array_pointer.contents, dtype=np.dtype(np.uint16)
  ).reshape(
    frame.contents.height, frame.contents.width
  ) # no copy

  # data = np.fromiter(
  #   frame.contents.data, dtype=np.dtype(np.uint8), count=frame.contents.data_bytes
  # ).reshape(
  #   frame.contents.height, frame.contents.width, 2
  # ) # copy

  if frame.contents.data_bytes != (2 * frame.contents.width * frame.contents.height):
    return

  if not q.full():
    q.put(data)

PTR_PY_FRAME_CALLBACK = CFUNCTYPE(None, POINTER(uvc_frame), c_void_p)(py_frame_callback)

def ktof(val):
  return (1.8 * ktoc(val) + 32.0)

def ktoc(val):
  return (val - 27315) / 100.0

# from https://groups.google.com/g/flir-lepton/c/LZUDqIXzuu8/m/A0Dz7lw-AAAJ
def compc(val):
  k_temp = 30250
  return 0.0217 * (val - 8192) + (k_temp/100) - 273.15

def raw_to_8bit(data):
  cv2.normalize(data, data, 0, 65535, cv2.NORM_MINMAX)
  np.right_shift(data, 8, data)
  return cv2.cvtColor(np.uint8(data), cv2.COLOR_GRAY2RGB)

def display_temperature(img, val_k, loc, color):
  val = compc(val_k)
  cv2.putText(img,"{0:.1f} C".format(val), loc, cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0,0,0), lineType=cv2.LINE_AA, thickness=4)
  cv2.putText(img,"{0:.1f} C".format(val), loc, cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, lineType=cv2.LINE_AA, thickness=2)
  x, y = loc
  size = 5
  cv2.line(img, (x - size, y), (x + size, y), color, 1)
  cv2.line(img, (x, y - size), (x, y + size), color, 1)

def display_timestamp(img, loc, timestamp):
  x, y = loc
  timestring = timestamp.isoformat(sep=" ", timespec='milliseconds')
  cv2.putText(img, timestring, loc, cv2.FONT_HERSHEY_PLAIN, 0.75, (0,0,0), lineType=cv2.LINE_AA, thickness=3)
  cv2.putText(img, timestring, loc, cv2.FONT_HERSHEY_PLAIN, 0.75, (255,255,255), lineType=cv2.LINE_AA, thickness=1)

def save_image(img, basename, starttime, frametime, interval):
  timediff = frametime - starttime
  seconds = timediff.total_seconds()
  interval_frac, interval_int = math.modf(interval)
  if interval_frac > 0:
    filename = "%s-%010.2f.jpg" % (basename, seconds)
  else:
    filename = "%s-%08d.jpg" % (basename, int(seconds))
  print("Saving image %s" % filename)
  cv2.imwrite(filename, img, [cv2.IMWRITE_JPEG_QUALITY, 50])

def telemetry(a):
  tel = {}
  a = a[0]
  b = a[80:]

  tel['tel_revision'] = a[0]
  tel['time_counter'] = a[1] + a[2]<<16
  tel['status'] = a[3] + a[4]<<16
  tel['serial'] = a[5:12]
  tel['revision'] = a[13:16]
  tel['frame_counter'] = a[20] + a[21]<<16
  tel['frame_mean'] = a[22]
  tel['fpa_temp_count'] = a[23]
  tel['fpa_temp_kelvin'] = a[24]
  tel['housing_temp_count'] = a[25]
  tel['housing_temp_kelvin'] = a[26]
  tel['fpa_temp_lastffc_kelvin'] = a[29]
  tel['time_counter_lastffc'] = a[30] + a[31]<<16
  tel['housing_temp_lastffc_kelvin'] = a[32]
  tel['emissivity'] = b[19]
  tel['background_temp_kelvin'] = b[20]
  tel['atmospheric_transmission'] = b[21]
  tel['atmospheric_temp'] = b[22]
  tel['window_transmission'] = b[23]
  tel['window_reflection'] = b[24]
  tel['window_temperature'] = b[25]
  tel['window_reflected'] = b[26]
  return tel

def main():
  parser = argparse.ArgumentParser(description='Capture and display Lepton images with temperature readings')
  parser.add_argument('--file', '-f', action='store', help="Save PNG images to sequential files beginning with this stem")
  parser.add_argument('--interval', '-i', action='store', help="Interval per saved frame, in seconds")
  args = parser.parse_args(sys.argv[1:])

  if args.interval:
    interval = float(args.interval)
  else:
    interval = 0

  ctx = POINTER(uvc_context)()
  dev = POINTER(uvc_device)()
  devh = POINTER(uvc_device_handle)()
  ctrl = uvc_stream_ctrl()

  res = libuvc.uvc_init(byref(ctx), 0)
  if res < 0:
    print("uvc_init error")
    exit(1)

  try:
    res = libuvc.uvc_find_device(ctx, byref(dev), PT_USB_VID, PT_USB_PID, 0)
    if res < 0:
      print("uvc_find_device error")
      exit(1)

    try:
      res = libuvc.uvc_open(dev, byref(devh))
      if res < 0:
        print("uvc_open error")
        exit(1)

      print("device opened!")

      print_device_info(devh)
      print_device_formats(devh)

      frame_formats = uvc_get_frame_formats_by_guid(devh, VS_FMT_GUID_Y16)
      if len(frame_formats) == 0:
        print("device does not support Y16")
        exit(1)

      idx = 1
      libuvc.uvc_get_stream_ctrl_format_size(devh, byref(ctrl), UVC_FRAME_FORMAT_Y16,
        frame_formats[idx].wWidth, frame_formats[idx].wHeight, int(1e7 / frame_formats[idx].dwDefaultFrameInterval)
      )

      res = libuvc.uvc_start_streaming(devh, byref(ctrl), PTR_PY_FRAME_CALLBACK, None, 0)
      if res < 0:
        print("uvc_start_streaming failed: {0}".format(res))
        exit(1)

      try:
        starttime = datetime.datetime.now()
        lastframetime = starttime
        while True:
          data = q.get(True, 500)
          if data is None:
            break
          frametime = datetime.datetime.now()
          telemetry_rows = data[-2:, :]
          tel = telemetry(telemetry_rows)
          print('\x0c')
          print(tel)
          data = cv2.resize(data[:-2,:], (640, 480))
          minVal, maxVal, minLoc, maxLoc = cv2.minMaxLoc(data)
          centreVal = data[240,320]
          img = raw_to_8bit(data)
          display_temperature(img, minVal, minLoc, (255, 255, 0))
          display_temperature(img, maxVal, maxLoc, (0, 0, 255))
          display_temperature(img, centreVal, (320, 240), (0, 255, 255))
          display_timestamp(img,(0,10), frametime)
          cv2.imshow('Lepton Radiometry', img)
          if args.file:
            timediff = frametime - lastframetime
            if (interval > 0 and timediff.total_seconds() > interval) or interval == 0:
              save_image(img, args.file, starttime, frametime, interval)
              lastframetime = frametime
          cv2.waitKey(1)

        cv2.destroyAllWindows()
      finally:
        libuvc.uvc_stop_streaming(devh)

      print("done")
    finally:
      libuvc.uvc_unref_device(dev)
  finally:
    libuvc.uvc_exit(ctx)

if __name__ == '__main__':
  main()
