import sys
import os
import time

import numpy as np
import cv2
from tqdm import tqdm

import ailia

# import original modules
sys.path.append('../../util')
from utils import get_base_parser, update_parser, get_savepath  # noqa: E402
from model_utils import check_and_download_models  # noqa: E402
from detector_utils import load_image  # noqa: E402
from image_utils import normalize_image  # noqa: E402C
import webcamera_utils  # noqa: E402

# logger
from logging import getLogger  # noqa: E402

logger = getLogger(__name__)

# ======================
# Parameters
# ======================

WEIGHT_PATH = 'cain.onnx'
MODEL_PATH = 'cain.onnx.prototxt'
REMOTE_PATH = 'https://storage.googleapis.com/ailia-models/cain/'

IMAGE_PATH = 'sample'
SAVE_IMAGE_PATH = 'output.png'

# ======================
# Arguemnt Parser Config
# ======================

parser = get_base_parser('CAIN', IMAGE_PATH, SAVE_IMAGE_PATH)
parser.add_argument(
    '-i2', '--input2', default=None,
    help='The second input image path.'
)
args = update_parser(parser, large_model=True)


# ======================
# Main functions
# ======================

def preprocess(img):
    img = normalize_image(img, normalize_type='255')

    img = img[:, :, ::-1]  # BGR -> RGB
    img = img.transpose(2, 0, 1)  # HWC -> CHW
    img = np.expand_dims(img, axis=0)
    img = img.astype(np.float32)

    return img


def post_processing(output):
    output = output.clip(0.0, 1.0)
    output = output.transpose((1, 2, 0)) * 255.0
    img = output.astype(np.uint8)
    img = img[:, :, ::-1]  # RGB -> BGR

    return img


def predict(net, img1, img2):
    img1 = preprocess(img1)
    img2 = preprocess(img2)

    # feedforward
    output = net.predict([img1, img2])
    out, feats = output

    out_img = post_processing(out[0])

    return out_img


def recognize_from_image(net):
    # Load images
    inputs = args.input
    n_input = len(inputs)
    if n_input == 1 and args.input2:
        inputs.extend([args.input2])

    if len(inputs) < 2:
        logger.error("Specified input must be at least two or more images")
        sys.exit(-1)

    for no, image_paths in enumerate(zip(inputs, inputs[1:])):
        logger.info(image_paths)

        # prepare input data
        images = [load_image(p) for p in image_paths]
        img1, img2 = [cv2.cvtColor(im, cv2.COLOR_BGRA2BGR) for im in images]

        # inference
        logger.info('Start inference...')
        if args.benchmark:
            logger.info('BENCHMARK mode')
            total_time_estimation = 0
            for i in range(args.benchmark_count):
                start = int(round(time.time() * 1000))
                out_img = predict(net, img1, img2)
                end = int(round(time.time() * 1000))
                estimation_time = (end - start)

                # Loggin
                logger.info(f'\tailia processing estimation time {estimation_time} ms')
                if i != 0:
                    total_time_estimation = total_time_estimation + estimation_time

            logger.info(f'\taverage time estimation {total_time_estimation / (args.benchmark_count - 1)} ms')
        else:
            out_img = predict(net, img1, img2)

        nm_ext = os.path.splitext(SAVE_IMAGE_PATH)
        save_file = "%s_%s%s" % (nm_ext[0], no, nm_ext[1])
        save_path = get_savepath(args.savepath, save_file, post_fix='', ext='.png')
        logger.info(f'saved at : {save_path}')
        cv2.imwrite(save_path, out_img)

    logger.info('Script finished successfully.')


def recognize_from_video(net):
    capture = webcamera_utils.get_capture(args.video)

    video_length = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    logger.info(f"video_length: {video_length}")

    # create video writer if savepath is specified as video format
    fps = int(capture.get(cv2.CAP_PROP_FPS))
    f_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    f_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    f_h = 256
    f_w = 448
    writer = None
    if args.savepath != SAVE_IMAGE_PATH:
        writer = webcamera_utils.get_writer(args.savepath, f_h, f_w, fps=fps)

    # create output buffer
    n_output = 1
    output_buffer = np.zeros((f_h * (n_output + 2), f_w, 3))
    output_buffer = output_buffer.astype(np.uint8)

    images = []
    if 0 < video_length:
        it = iter(tqdm(range(video_length)))
        next(it)
    while True:
        if 0 < video_length:
            try:
                next(it)
            except StopIteration:
                break
        ret, frame = capture.read()
        if (cv2.waitKey(1) & 0xFF == ord('q')) or not ret:
            break

        # inputs.append(frame)
        images.append(cv2.resize(frame, (f_w, f_h)))
        if len(images) < 2:
            continue
        elif len(images) > 2:
            images = images[1:]

        img1, img2 = images
        out_img = predict(net, img1, img2)

        # save results
        if writer is not None:
            writer.write(images[0])
        output_buffer[:f_h, :f_w, :] = images[0]
        output_buffer[f_h * 2:f_h * 3, :f_w, :] = images[1]

        if writer is not None:
            writer.write(out_img)
        output_buffer[f_h:f_h * 2, :f_w, :] = out_img

        # preview
        cv2.imshow('frame', output_buffer)

    capture.release()
    cv2.destroyAllWindows()
    if writer is not None:
        writer.release()
    logger.info('Script finished successfully.')


def main():
    # model files check and download
    check_and_download_models(WEIGHT_PATH, MODEL_PATH, REMOTE_PATH)

    # net initialize
    net = ailia.Net(MODEL_PATH, WEIGHT_PATH, env_id=args.env_id)

    if args.video is not None:
        # video mode
        recognize_from_video(net)
    else:
        # image mode
        recognize_from_image(net)


if __name__ == '__main__':
    main()
