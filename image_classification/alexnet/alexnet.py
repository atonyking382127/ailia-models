import time
import sys
import cv2
import numpy as np
import ailia

# import original modules
sys.path.append('../../util')
from utils import get_base_parser, update_parser  # noqa: E402
from model_utils import check_and_download_models  # noqa: E402
from image_utils import load_image  # noqa: E402
from classifier_utils import plot_results, print_results  # noqa: E402

# logger
from logging import getLogger  # noqa: E402
logger = getLogger(__name__)


# ======================
# PARAMETERS
# ======================
MODEL_PATH  = "alexnet.onnx.prototxt"
WEIGHT_PATH = "alexnet.onnx"
REMOTE_PATH = "https://storage.googleapis.com/ailia-models/alexnet/"
IMAGE_PATH = "input/dog.jpg"
IMAGE_HEIGHT = 224
IMAGE_WIDTH = 224


# ======================
# Argument Parser Config
# ======================
parser = get_base_parser("Alexnet is ", IMAGE_PATH, None,)
args = update_parser(parser)


# ======================
# Main functions
# ======================
def _get_image(filename):
    from PIL import Image
    from torchvision import transforms
    import torch
    input_image = Image.open(filename)
    preprocess = transforms.Compose([
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    input_tensor = preprocess(input_image)
    input_batch = input_tensor.unsqueeze(0) # create a mini-batch as expected by the model
    input_batch = input_batch.to('cpu').detach().numpy().copy()
    return input_batch


def _get_prob(output, topk=5):
    from PIL import Image
    from torchvision import transforms
    import torch
    output = torch.from_numpy(output)
    prob = torch.nn.functional.softmax(output[0], dim=0)
    #prob = prob.to('cpu').detach().numpy().copy()
    topk_prob, topk_catid = torch.topk(prob, topk)
    topk_prob = topk_prob.to('cpu').detach().numpy().copy()
    topk_catid = topk_catid.to('cpu').detach().numpy().copy()
    return topk_prob, topk_catid


def _get_labels():
    categories = []
    with open("imagenet_classes.txt", "r") as f:
        categories = [s.strip() for s in f.readlines()]
    return categories


def recognize_from_image():
    net = ailia.Net(MODEL_PATH, WEIGHT_PATH, env_id=args.env_id)
    labels = _get_labels()

    # input image loop
    for i, image_path in enumerate(args.input):
        input_batch = _get_image(image_path)
        output = net.predict(input_batch)
        topk_prob, topk_catid = _get_prob(output)
        print('[Image_{}] {}'.format(i+1, image_path))
        for k in range(topk_prob.shape[0]):
            print('\t{} {}'.format(labels[topk_catid[k]], topk_prob[k]))

    logger.info('Script finished successfully.')


def main():
    # model files check and download
    check_and_download_models(WEIGHT_PATH, MODEL_PATH, REMOTE_PATH)

    # recognize
    recognize_from_image()


if __name__ == '__main__':
    main()
