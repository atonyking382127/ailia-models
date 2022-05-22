import sys
import time

import numpy as np
import cv2
from PIL import Image

import ailia

# import original modules
sys.path.append('../../util')
from utils import get_base_parser, update_parser, get_savepath  # noqa
from model_utils import check_and_download_models  # noqa
from detector_utils import load_image  # noqa
from image_utils import normalize_image  # noqa
from webcamera_utils import get_capture, get_writer  # noqa
# logger
from logging import getLogger  # noqa

import face_detect_crop
from face_detect_crop import crop_face, get_kps
import face_align

logger = getLogger(__name__)

# ======================
# Parameters
# ======================

WEIGHT_G_PATH = 'G_unet_2blocks.onnx'
MODEL_G_PATH = 'G_unet_2blocks.onnx.prototxt'
WEIGHT_ARCFACE_PATH = 'scrfd_10g_bnkps.onnx'
MODEL_ARCFACE_PATH = 'scrfd_10g_bnkps.onnx.prototxt'
WEIGHT_BACKBONE_PATH = 'arcface_backbone.onnx'
MODEL_BACKBONE_PATH = 'arcface_backbone.onnx.prototxt'
REMOTE_PATH = 'https://storage.googleapis.com/ailia-models/sber-swap/'

IMAGE_PATH = 'beckham.jpg'
SOURCE_PATH = 'elon_musk.jpg'
SAVE_IMAGE_PATH = 'output.png'

CROP_SIZE = 224

IMAGE_HEIGHT = 224
IMAGE_WIDTH = 224

THRESHOLD = 0.4
IOU = 0.45

# ======================
# Arguemnt Parser Config
# ======================

parser = get_base_parser(
    'SberSwap', IMAGE_PATH, SAVE_IMAGE_PATH
)
parser.add_argument(
    '-src', '--source', default=SOURCE_PATH,
    help='source image'
)
parser.add_argument(
    '--onnx',
    action='store_true',
    help='execute onnxruntime version.'
)
args = update_parser(parser)


# ======================
# Secondaty Functions
# ======================


# def draw_bbox(img, bboxes):
#     return img


# ======================
# Main functions
# ======================

def preprocess(img, half_scale=True):
    # if half_scale:
    #     im_h, im_w, _ = img.shape
    #     img = np.array(Image.fromarray(img).resize((im_w // 2, im_h // 2), Image.BILINEAR))

    img = normalize_image(img, normalize_type='127.5')

    img = img.transpose(2, 0, 1)  # HWC -> CHW
    img = np.expand_dims(img, axis=0)
    img = img.astype(np.float32)

    if half_scale:
        import torch
        import torch.nn.functional as F
        img = F.interpolate(torch.from_numpy(img), scale_factor=0.5, mode='bilinear', align_corners=True).numpy()

    return img


def post_processing(output):
    return None


def predict(net_iface, net_back, net_G, src_embeds, tar_img):
    kps = get_kps(tar_img, net_iface)

    M, _ = face_align.estimate_norm(kps[0], CROP_SIZE, mode='None')
    img = cv2.warpAffine(tar_img, M, (CROP_SIZE, CROP_SIZE), borderValue=0.0)

    # target embeds
    _img = preprocess(img)
    if not args.onnx:
        output = net_back.predict([_img])
    else:
        output = net_back.run(None, {'img': _img})
    target_embeds = output[0]

    new_size = (256, 256)
    img = cv2.resize(img, new_size)

    _img = preprocess(img[:, :, ::-1], half_scale=False)
    _img = _img.astype(np.float16)
    if not args.onnx:
        output = net_G.predict([_img, src_embeds])
    else:
        output = net_G.run(None, {'target': _img, 'source_emb': src_embeds})
    y_st = output[0]

    # pred = post_processing(output)

    # return pred

    return 0


def recognize_from_image(net_iface, net_back, net_G):
    source_path = args.source
    logger.info('SOURCE: {}'.format(source_path))

    src_img = load_image(source_path)
    src_img = cv2.cvtColor(src_img, cv2.COLOR_BGRA2BGR)

    src_img = crop_face(src_img, net_iface, CROP_SIZE)
    src_img = src_img[:, :, ::-1]  # BGR -> RGB

    # source embeds
    img = preprocess(src_img)
    if not args.onnx:
        output = net_back.predict([img])
    else:
        output = net_back.run(None, {'img': img})
    src_embeds = output[0]
    src_embeds = src_embeds.astype(np.float16)

    # input image loop
    for image_path in args.input:
        logger.info(image_path)

        # prepare input data
        tar_img = load_image(image_path)
        tar_img = cv2.cvtColor(tar_img, cv2.COLOR_BGRA2BGR)

        # inference
        logger.info('Start inference...')
        if args.benchmark:
            logger.info('BENCHMARK mode')
            total_time_estimation = 0
            for i in range(args.benchmark_count):
                start = int(round(time.time() * 1000))
                out = predict(net_iface, net_back, net_G, src_embeds, tar_img)
                end = int(round(time.time() * 1000))
                estimation_time = (end - start)

                # Loggin
                logger.info(f'\tailia processing estimation time {estimation_time} ms')
                if i != 0:
                    total_time_estimation = total_time_estimation + estimation_time

            logger.info(f'\taverage time estimation {total_time_estimation / (args.benchmark_count - 1)} ms')
        else:
            out = predict(net_iface, net_back, net_G, src_embeds, tar_img)

        # res_img = draw_bbox(out)
        #
        # # plot result
        # savepath = get_savepath(args.savepath, image_path, ext='.png')
        # logger.info(f'saved at : {savepath}')
        # cv2.imwrite(savepath, res_img)

    logger.info('Script finished successfully.')


def recognize_from_video(net_iface, net_back, net_G):
    video_file = args.video if args.video else args.input[0]
    capture = get_capture(video_file)
    assert capture.isOpened(), 'Cannot capture source'

    # create video writer if savepath is specified as video format
    if args.savepath != SAVE_IMAGE_PATH:
        f_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        f_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        writer = get_writer(args.savepath, f_h, f_w)
    else:
        writer = None

    frame_shown = False
    while True:
        ret, frame = capture.read()
        if (cv2.waitKey(1) & 0xFF == ord('q')) or not ret:
            break
        if frame_shown and cv2.getWindowProperty('frame', cv2.WND_PROP_VISIBLE) == 0:
            break

        # inference
        img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        out = predict(net_G, img)

        # # plot result
        # res_img = draw_bbox(frame, out)
        #
        # # show
        # cv2.imshow('frame', res_img)
        # frame_shown = True
        #
        # # save results
        # if writer is not None:
        #     res_img = res_img.astype(np.uint8)
        #     writer.write(res_img)

    capture.release()
    cv2.destroyAllWindows()
    if writer is not None:
        writer.release()

    logger.info('Script finished successfully.')


def main():
    # model files check and download
    logger.info('Checking G model...')
    check_and_download_models(WEIGHT_G_PATH, MODEL_G_PATH, REMOTE_PATH)
    logger.info('Checking arcface model...')
    check_and_download_models(WEIGHT_ARCFACE_PATH, MODEL_ARCFACE_PATH, REMOTE_PATH)
    logger.info('Checking backbone model...')
    check_and_download_models(WEIGHT_BACKBONE_PATH, MODEL_BACKBONE_PATH, REMOTE_PATH)

    env_id = args.env_id

    # initialize
    if not args.onnx:
        net_iface = ailia.Net(MODEL_ARCFACE_PATH, WEIGHT_ARCFACE_PATH, env_id=env_id)
        net_back = ailia.Net(MODEL_BACKBONE_PATH, WEIGHT_BACKBONE_PATH, env_id=env_id)
        net_G = ailia.Net(MODEL_G_PATH, WEIGHT_G_PATH, env_id=env_id)
    else:
        import onnxruntime
        net_iface = onnxruntime.InferenceSession(WEIGHT_ARCFACE_PATH)
        net_back = onnxruntime.InferenceSession(WEIGHT_BACKBONE_PATH)
        net_G = onnxruntime.InferenceSession(WEIGHT_G_PATH)
        face_detect_crop.onnx = True

    if args.video is not None:
        recognize_from_video(net_iface, net_back, net_G)
    else:
        recognize_from_image(net_iface, net_back, net_G)


if __name__ == '__main__':
    main()
