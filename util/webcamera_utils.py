import sys

import numpy as np
import cv2

from utils import check_file_existance
from image_utils import normalize_image


def calc_adjust_fsize(f_height, f_width, height, width):
    # calculate the image size of the output('img') of adjust_frame_size
    # This function is supposed to be used to declare 'cv2.writer'
    scale = np.max((f_height / height, f_width / width))
    return int(scale * height), int(scale * width)


def adjust_frame_size(frame, height, width):
    """
    Adjust the size of the frame from the webcam to the ailia input shape.

    Parameters
    ----------
    frame: numpy array
    height: int
        ailia model input height
    width: int
        ailia model input width

    Returns
    -------
    img: numpy array
        Image with the propotions of height and width
        adjusted by padding for ailia model input.
    resized_img: numpy array
        Resized `img` as well as adapt the scale
    """
    f_height, f_width = frame.shape[0], frame.shape[1]
    scale = np.max((f_height / height, f_width / width))

    # padding base
    img = np.zeros(
        (int(round(scale * height)), int(round(scale * width)), 3),
        np.uint8
    )
    start = (np.array(img.shape) - np.array(frame.shape)) // 2
    img[
        start[0]: start[0] + f_height,
        start[1]: start[1] + f_width
    ] = frame
    resized_img = cv2.resize(img, (width, height))
    return img, resized_img


def preprocess_frame(
        frame, input_height, input_width, data_rgb=True, normalize_type='255'
):
    """
    Pre-process the frames taken from the webcam to input to ailia.

    Parameters
    ----------
    frame: numpy array
    input_height: int
        ailia model input height
    input_width: int
        ailia model input width
    data_rgb: bool (default: True)
        Convert as rgb image when True, as gray scale image when False.
        Only `data` will be influenced by this configuration.
    normalize_type: string (default: 255)
        Normalize type should be chosen from the type below.
        - '255': simply dividing by 255.0
        - '127.5': output range : -1 and 1
        - 'ImageNet': normalize by mean and std of ImageNet
        - 'None': no normalization

    Returns
    -------
    img: numpy array
        Image with the propotions of height and width
        adjusted by padding for ailia model input.
    data: numpy array
        Input data for ailia
    """
    img, resized_img = adjust_frame_size(frame, input_height, input_width)

    if data_rgb:
        resized_img = cv2.cvtColor(resized_img, cv2.COLOR_BGR2RGB)

    data = normalize_image(resized_img, normalize_type)

    if data_rgb:
        data = np.rollaxis(data, 2, 0)
        data = np.expand_dims(data, axis=0).astype(np.float32)
    else:
        data = cv2.cvtColor(data.astype(np.float32), cv2.COLOR_BGR2GRAY)
        data = data[np.newaxis, np.newaxis, :, :]
    return img, data


def get_writer(savepath, height, width, rgb=True):
    writer = cv2.VideoWriter(
        savepath,
        cv2.VideoWriter_fourcc(*'MJPG'),
        20,
        (width, height),
        isColor=rgb
    )
    return writer


def get_capture(video):
    """
    Get cv2.VideoCapture

    Parameters
    ----------
    video : str
        webcamera-id or video path

    Returns
    -------
    capture : cv2.VideoCapture
    """
    try:
        video_id = int(video)

        # webcamera-mode
        capture = cv2.VideoCapture(video_id)
        if not capture.isOpened():
            print(f"[ERROR] webcamera (ID - {video_id}) not found")
            sys.exit(0)

    except ValueError:
        # if file path is given, open video file
        if check_file_existance(video):
            capture = cv2.VideoCapture(video)

    return capture
