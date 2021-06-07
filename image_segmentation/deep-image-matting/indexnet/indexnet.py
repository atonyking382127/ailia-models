import os
import sys
import time

import numpy as np
import cv2

import ailia
# import original modules
sys.path.append('../../../util')
from utils import get_base_parser, update_parser, get_savepath  # noqa: E402
from model_utils import check_and_download_models  # noqa: E402
import webcamera_utils  # noqa: E402

# logger
from logging import getLogger   # noqa: E402
logger = getLogger(__name__)


# ======================
# Parameters
# ======================
REMOTE_PATH = 'https://storage.googleapis.com/ailia-models/indexnet/'

IMAGE_PATH = 'input.jpg'
TRIMAP_PATH = 'trimap.png'

SAVE_IMAGE_PATH = 'output.png'

MODEL_LISTS = ['u2net']

FRAMEWORK_LISTS = ['torch']


# ======================
# Argument Parser Config
# ======================
parser = get_base_parser('Indexnet', IMAGE_PATH, SAVE_IMAGE_PATH)
parser.add_argument(
    '-t', '--trimap', metavar='IMAGE',
    default=TRIMAP_PATH,
    help='The input trimap image path.'
)
parser.add_argument(
    '-a', '--arch', metavar='ARCH',
    default='u2net', choices=MODEL_LISTS,
    help='model lists: ' + ' | '.join(MODEL_LISTS)
)
parser.add_argument(
    '-f', '--framework', metavar='FRAMEWORK',
    default='torch', choices=FRAMEWORK_LISTS,
    help='framework lists: ' + ' | '.join(FRAMEWORK_LISTS)
)
parser.add_argument(
    '-d', '--debug',
    action='store_true',
    help='Dump debug images'
)
parser.add_argument(
    '-n', '--onnx', 
    action='store_true',
    default=False,
    help='Use onnxruntime'
)
args = update_parser(parser)

if args.arch == 'u2net':
    SEGMENTATION_WEIGHT_PATH = 'u2net_opset11.onnx'
    SEGMENTATION_MODEL_PATH = SEGMENTATION_WEIGHT_PATH + '.prototxt'
    SEGMENTATION_REMOTE_PATH = \
        'https://storage.googleapis.com/ailia-models/u2net/'

WEIGHT_PATH = 'indexnet.onnx'
MODEL_PATH = 'indexnet.onnx.prototxt'

# ======================
# Utils
# ======================

#crop_pos=(x,y), crop_size=(height,width)
def safe_crop(mat, crop_pos=(0,0), crop_size=(0,0)):
    # destination buffer
    crop_height, crop_width = crop_size
    if len(mat.shape) == 2:
        ret = np.zeros((crop_height, crop_width), np.float32)
    else:
        ret = np.zeros((crop_height, crop_width, 3), np.float32)

    # capture from
    x = crop_pos[0]
    y = crop_pos[1]

    # copy to
    dx = 0
    dy = 0

    # treat negative region
    if x<0:
        dx=-x
        crop_width=crop_width+x
        x=0
    if y<0:
        dy=-y
        crop_height=crop_height+y
        y=0

    # copy
    crop = mat[y:y + crop_height, x:x + crop_width]
    h, w = crop.shape[:2]
    ret[dy:dy+h, dx:dx+w] = crop
    return ret

def get_final_output(out, trimap):
    # based pytorch version
    # supporting various unknown_code
    out[trimap==0] = 0
    out[trimap==255] = 255.0
    return out

def postprocess(src_img, trimap, preds_ailia, h, w):
    trimap = trimap[:, :, 0].reshape((h, w))

    preds_ailia = preds_ailia.reshape((h, w))
    preds_ailia = preds_ailia * 255.0
    preds_ailia = get_final_output(preds_ailia, trimap)

    output_data = np.zeros((h, w, 4))
    output_data[:, :, 0] = src_img[:, :, 0]
    output_data[:, :, 1] = src_img[:, :, 1]
    output_data[:, :, 2] = src_img[:, :, 2]
    output_data[:, :, 3] = preds_ailia

    output_data[output_data > 255] = 255
    output_data[output_data < 0] = 0

    return output_data

def matting_preprocess(src_img, trimap_data, seg_data, h, w):
    input_data = np.zeros((1, h, w, 4))
    input_data[:, :, :, 0:3] = src_img[:, :, 0:3]
    input_data[:, :, :, 3] = trimap_data[:, :, 0]

    savedir = os.path.dirname(args.savepath)
    if args.debug:
        cv2.imwrite(
            os.path.join(savedir, "debug_input.png"),
            input_data.reshape((h, w, 4)),
        )

    input_data = input_data / 255.0

    return input_data, src_img, trimap_data

def composite(img):
    img[:, :, 0] = img[:, :, 0] * img[:, :, 3] / 255.0
    img[:, :, 1] = img[:, :, 1] * img[:, :, 3] / \
        255.0 + 255.0 * (1.0 - img[:, :, 3] / 255.0)
    img[:, :, 2] = img[:, :, 2] * img[:, :, 3] / 255.0
    return img

# ======================
# Segmentation util
# ======================

def norm(pred):
    ma = np.max(pred)
    mi = np.min(pred)
    return (pred - mi) / (ma - mi)

def erode_and_dilate(mask, k_size, ite):
    kernel = np.ones(k_size, np.uint8)
    eroded = cv2.erode(mask, kernel, iterations=ite)
    dilated = cv2.dilate(mask, kernel, iterations=ite)
    trimap = np.full(mask.shape, 128)
    trimap[eroded >= 254] = 255
    trimap[dilated <= 1] = 0
    return trimap

def imagenet_preprocess(input_data):
    input_data = input_data / 255.0
    input_data[:, :, 0] = (input_data[:, :, 0]-0.485)/0.229
    input_data[:, :, 1] = (input_data[:, :, 1]-0.456)/0.224
    input_data[:, :, 2] = (input_data[:, :, 2]-0.406)/0.225
    input_data = input_data.transpose((2, 0, 1))[np.newaxis, :, :, :]
    return input_data

def generate_trimap(net, input_data):
    src_data = input_data.copy()
    
    h = input_data.shape[0]
    w = input_data.shape[1]

    input_shape = net.get_input_shape()
    input_data = cv2.resize(input_data, (input_shape[2], input_shape[3]))

    if args.arch == "u2net":
        input_data = imagenet_preprocess(input_data)

    preds_ailia = net.predict([input_data])

    if args.arch == "u2net":
        pred = preds_ailia[0][0, 0, :, :]

    if args.debug:
        dump_segmentation(pred, src_data, w, h)

    if args.arch == "u2net":
        pred = norm(pred)

    trimap_data = cv2.resize(pred * 255, (w, h))
    trimap_data = trimap_data.reshape((h, w, 1))

    seg_data = trimap_data.copy()

    thre = 0.6
    ite = 3

    if args.arch == "u2net":
        thre = 0.8
        ite = 5

    thre = 255 * thre
    trimap_data[trimap_data < thre] = 0
    trimap_data[trimap_data >= thre] = 255

    trimap_data = trimap_data.astype("uint8")

    if args.debug:
        dump_segmentation_threshold(trimap_data, src_data, w, h)

    trimap_data = erode_and_dilate(trimap_data, k_size=(7, 7), ite=ite)

    if args.debug:
        dump_trimap(trimap_data, src_data, w, h)

    return trimap_data, seg_data

# ======================
# Debug functions
# ======================

def dump_segmentation(pred, src_data, w, h):
    savedir = os.path.dirname(args.savepath)
    segmentation_data = cv2.resize(pred * 255, (w, h))
    segmentation_data = cv2.cvtColor(segmentation_data, cv2.COLOR_GRAY2BGR)
    segmentation_data = (src_data + segmentation_data)/2
    cv2.imwrite(os.path.join(savedir, "debug_segmentation.png"), segmentation_data)


def dump_segmentation_threshold(trimap_data, src_data, w, h):
    savedir = os.path.dirname(args.savepath)
    segmentation_data = trimap_data.copy()
    segmentation_data = cv2.cvtColor(segmentation_data, cv2.COLOR_GRAY2BGR)
    segmentation_data = segmentation_data.astype(np.float)
    segmentation_data = (src_data + segmentation_data)/2
    cv2.imwrite(
        os.path.join(savedir, "debug_segmentation_threshold.png"),
        segmentation_data,
    )


def dump_trimap(trimap_data, src_data, w, h):
    savedir = os.path.dirname(args.savepath)

    cv2.imwrite(
        os.path.join(savedir, "debug_trimap_gray.png"),
        trimap_data,
    )

    segmentation_data = trimap_data.copy().astype(np.uint8)
    segmentation_data = cv2.cvtColor(segmentation_data, cv2.COLOR_GRAY2BGR)
    segmentation_data = segmentation_data.astype(np.float)
    segmentation_data = (src_data + segmentation_data)/2

    cv2.imwrite(
        os.path.join(savedir, "debug_trimap.png"),
        segmentation_data,
    )


def dump_output(preds_ailia, trimap_crop_data, h, w):
    savedir = os.path.dirname(args.savepath)

    preds_ailia=preds_ailia.reshape((h,w))
    output = (preds_ailia*255).astype(np.uint8)
    output = cv2.cvtColor(output, cv2.COLOR_GRAY2BGR)
    cv2.imwrite(os.path.join(savedir, "debug_output.png"), output)

    output = get_final_output(preds_ailia*255, trimap_crop_data[:, :, 0].reshape((h, w)))
    output = output.astype(np.uint8)
    output = cv2.cvtColor(output, cv2.COLOR_GRAY2BGR)
    cv2.imwrite(os.path.join(savedir, "debug_output_masked.png"), output)

    output = (trimap_crop_data).astype(np.uint8)
    cv2.imwrite(os.path.join(savedir, "debug_trimap_input.png"), output)

# ======================
# Process one frame
# ======================

def process_one_frame(output_img,src_img,trimap_data,IMAGE_WIDTH,IMAGE_HEIGHT,PADDING,net):
    for y in range((src_img.shape[0]+IMAGE_HEIGHT-PADDING*2-1)//(IMAGE_HEIGHT-PADDING*2)):
        for x in range((src_img.shape[1]+IMAGE_WIDTH-PADDING*2-1)//(IMAGE_WIDTH-PADDING*2)):
            logger.info('Tile ('+str(x)+','+str(y)+')')

            # crop
            crop_size = (IMAGE_HEIGHT, IMAGE_WIDTH)
            crop_pos = (x * (IMAGE_WIDTH-PADDING*2) - PADDING, y * (IMAGE_HEIGHT-PADDING*2) - PADDING)

            src_crop_img = safe_crop(src_img, crop_pos, crop_size)
            trimap_crop_data = safe_crop(trimap_data, crop_pos, crop_size)

            seg_data = trimap_crop_data.copy()

            input_data, src_crop_img, trimap_crop_data = matting_preprocess(
                src_crop_img, trimap_crop_data, seg_data, IMAGE_HEIGHT, IMAGE_WIDTH
            )

            # torch channel order
            if args.framework=="torch":
                input_data = input_data.transpose((0, 3, 1, 2))

            # inference
            logger.info('Start inference...')
            if args.benchmark:
                logger.info('BENCHMARK mode')
                for i in range(5):
                    start = int(round(time.time() * 1000))
                    preds_ailia = net.predict(input_data)
                    end = int(round(time.time() * 1000))
                    logger.info(f'\tailia processing time {end - start} ms')
            else:
                if args.onnx:
                    first_input_name = net.get_inputs()[0].name
                    input_data = input_data.astype(np.float32)
                    preds_ailia = net.run([], {first_input_name: input_data})[0]
                else:
                    preds_ailia = net.predict(input_data)

            # dump output
            if args.debug:
                dump_output(preds_ailia,trimap_crop_data,IMAGE_HEIGHT,IMAGE_WIDTH)

            # post-processing
            res_img = postprocess(src_crop_img, trimap_crop_data, preds_ailia, IMAGE_HEIGHT, IMAGE_WIDTH)

            # copy
            active_pos=(crop_pos[0]+PADDING,crop_pos[1]+PADDING)
            ch = res_img.shape[0]-PADDING*2
            cw = res_img.shape[1]-PADDING*2
            if active_pos[0] + cw >= output_img.shape[1]:
                cw = output_img.shape[1] - active_pos[0]
            if active_pos[1] + ch >= output_img.shape[0]:
                ch = output_img.shape[0] - active_pos[1]
            output_img[active_pos[1]:active_pos[1]+ch,active_pos[0]:active_pos[0]+cw,:] = res_img[PADDING:PADDING+ch,PADDING:PADDING+cw,:]

def set_input_shape(net, src_img):
    
    IMAGE_WIDTH = (src_img.shape[1]+31)//32*32
    IMAGE_HEIGHT = (src_img.shape[0]+31)//32*32  
    PADDING = 0
    RGB_MODE = True
    if not args.onnx:
        net.set_input_shape((1, 4, IMAGE_HEIGHT, IMAGE_WIDTH))
    return IMAGE_WIDTH,IMAGE_HEIGHT,PADDING,RGB_MODE

# ======================
# Main functions
# ======================

def recognize_from_image(net):
    # trimap mode
    if args.trimap == "":
        seg_net = ailia.Net(
            SEGMENTATION_MODEL_PATH,
            SEGMENTATION_WEIGHT_PATH,
            env_id=args.env_id,
        )

    # input image loop
    for image_path in args.input:
        # prepare input data
        logger.info(image_path)
        src_img = cv2.imread(image_path)    

        # set input shape
        IMAGE_WIDTH, IMAGE_HEIGHT, PADDING, RGB_MODE = set_input_shape(net, src_img)

        if RGB_MODE:
            src_img = cv2.cvtColor(src_img, cv2.COLOR_BGR2RGB)

        # create trimap
        if args.trimap == "":
            input_data = src_img
            trimap_data, seg_data = generate_trimap(seg_net, input_data)
        else:
            trimap_data = cv2.imread(args.trimap)

        # output image buffer
        output_img = np.zeros((src_img.shape[0],src_img.shape[1],4))

        # tile loop
        process_one_frame(output_img,src_img,trimap_data,IMAGE_WIDTH,IMAGE_HEIGHT,PADDING,net)
        
        # save
        if RGB_MODE:
            output_img = output_img.astype(np.uint8)
            output_img = cv2.cvtColor(output_img, cv2.COLOR_RGBA2BGRA)

        savepath = get_savepath(args.savepath, image_path)
        logger.info(f'saved at : {savepath}')
        cv2.imwrite(savepath, output_img)

    logger.info('Script finished successfully.')


def recognize_from_video(net):
    # segmentation net
    seg_net = ailia.Net(
        SEGMENTATION_MODEL_PATH,
        SEGMENTATION_WEIGHT_PATH,
        env_id=args.env_id,
    )

    capture = webcamera_utils.get_capture(args.video)
    writer = None

    while(True):
        ret, frame = capture.read()
        if (cv2.waitKey(1) & 0xFF == ord('q')) or not ret:
            break

        # grab src image
        src_img = frame

        # limit resolution
        w_limit = 640
        if src_img.shape[0]>=w_limit:
            src_img = cv2.resize(src_img,(w_limit,int(w_limit*src_img.shape[0]/src_img.shape[1])))

       # set input shape
        IMAGE_WIDTH, IMAGE_HEIGHT, PADDING, RGB_MODE = set_input_shape(net, src_img)

        # output image buffer
        output_img = np.zeros((src_img.shape[0],src_img.shape[1],4))

        # color conversion
        if RGB_MODE:
            src_img = cv2.cvtColor(src_img, cv2.COLOR_BGR2RGB)

        # create video writer if savepath is specified as video format
        if args.savepath != SAVE_IMAGE_PATH:
            if writer==None:
                writer = webcamera_utils.get_writer(
                    args.savepath, output_img.shape[0], output_img.shape[1]
                )

        # generate trimap
        trimap_data, seg_data = generate_trimap(seg_net, src_img)
        cv2.imshow('trimap', trimap_data / 255.0)
        cv2.imshow('segmentation', seg_data / 255.0)

        # tile loop
        process_one_frame(output_img,src_img,trimap_data,IMAGE_WIDTH,IMAGE_HEIGHT,PADDING,net)
        output_img = composite(output_img)
        
        # save
        output_img = output_img.astype(np.uint8)
        if RGB_MODE:
            output_img = cv2.cvtColor(output_img, cv2.COLOR_RGBA2BGRA)

        cv2.imshow('frame', output_img)

        # save results
        if writer is not None:
            writer.write(output_img[:,:,0:3])

    capture.release()
    cv2.destroyAllWindows()
    if writer is not None:
        writer.release()
    logger.info('Script finished successfully.')


def main():
    # model files check and download
    check_and_download_models(WEIGHT_PATH, MODEL_PATH, REMOTE_PATH)
    if args.trimap == "":
        check_and_download_models(
            SEGMENTATION_WEIGHT_PATH,
            SEGMENTATION_MODEL_PATH,
            SEGMENTATION_REMOTE_PATH,
        )

    # net initialize
    if args.onnx:
        import onnxruntime
        net = onnxruntime.InferenceSession(WEIGHT_PATH)
    else:
        net = ailia.Net(MODEL_PATH, WEIGHT_PATH)

    if args.video is not None:
        # video mode
        recognize_from_video(net)
    else:
        # image mode
        recognize_from_image(net)


if __name__ == '__main__':
    main()
