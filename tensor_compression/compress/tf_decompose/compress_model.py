"""
This module contains functions for compressing fully-connected and conv layers.
"""
import tensorflow as tf

from absl import logging
from tensorflow.keras.models import Model
from tensorflow import keras
# from svd_layer import get_svd_seq, SVDLayer
from cp3_decomposition import get_cp3_seq
from cp4_decomposition import get_cp4_seq
from svd_decomposition import get_svd_seq
from tucker2_decomposition import get_tucker2_seq

# from utils import get_subgraph


def get_compressed_sequential(model, decompose_info, optimize_rank=False, vbmf=True, vbmf_weaken_factor=0.8):
    """Compresses source model using decompositions from decompose_info dict.

    For example if decompose_info = {
            'dense': ('svd', 10)
    }
    it means that the layer with the name 'dense' will be compressed
    using TruncatedSVD with truncation rank 10.

    For fully-connected layer you can use SVD decomposition
    For convolution layer networks CP3, CP4, Tucker-2 are available.

    If you want learn more about different tensor decomposition refer:

    'Tensor Networks for Dimensionality Reduction and Large-Scale Optimization.
    Part 1 Low-Rank Tensor Decompositions.'

    :param model: source model.
    :param decompose_info: dict that describes what layers compress using what decomposition method.
                           Possible decompositions are: 'svd', 'cp3', 'cp4', 'tucker-2'.
    :return: new tf.keras.Model with compressed layers.
    """
    x = model.input
    new_model = keras.Sequential([])
    for idx, layer in enumerate(model.layers):
        if layer.name not in decompose_info:
            x = layer(x)
            new_model.add(layer)
            continue

        decompose, decomp_rank = decompose_info[layer.name]
        if decompose.lower() == 'svd':
            logging.info('SVD layer {}'.format(layer.name))
            new_layer = get_svd_seq(layer, rank=decomp_rank)
        elif decompose.lower() == 'cp3':
            logging.info('CP3 layer {}'.format(layer.name))
            new_layer = get_cp3_seq(layer,
                                    rank=decomp_rank,
                                    optimize_rank=optimize_rank)
        elif decompose.lower() == 'cp4':
            logging.info('CP4 layer {}'.format(layer.name))
            new_layer = get_cp4_seq(layer,
                                    rank=decomp_rank,
                                    optimize_rank=optimize_rank)
        elif decompose.lower() == 'tucker2':
            logging.info('Tucker2 layer {}'.format(layer.name))
            new_layer = get_tucker2_seq(layer,
                                        rank=decomp_rank,
                                        optimize_rank=optimize_rank,
                                        vbmf=vbmf,
                                        vbmf_weaken_factor=vbmf_weaken_factor)
        else:
            logging.info('Incorrect decomposition type for the layer {}'.format(layer.name))
            raise NameError("Wrong Decomposition Name. You should use one of: ['svd', 'cp3', 'cp4', 'tucker-2']")

        x = new_layer(x)
        new_model.add(new_layer)

    return new_model


def insert_layer_nonseq(model, layer_regexs):
    # Auxiliary dictionary to describe the network graph
    network_dict = {'input_layers_of': {}, 'new_output_tensor_of': {}}
    current_session = tf.keras.backend.get_session()
    # Set the input layers of each layer
    for layer in model.layers:
        for node in layer.outbound_nodes:
            layer_name = node.outbound_layer.name

            if layer_name not in network_dict['input_layers_of']:
                network_dict['input_layers_of'].update(
                    {layer_name: [layer.name]})
            else:
                network_dict['input_layers_of'][layer_name].append(layer.name)

    # Set the output tensor of the input layer
    network_dict['new_output_tensor_of'].update(
        {model.layers[0].name: model.input})

    # Iterate over all layers after the input
    conenctions = dict({model.layers[0].name: (model.layers[0].__class__,
                                               model.layers[0].get_config(),
                                               None)})
    layers_order = [model.layers[0].name]
    for layer in model.layers[1:]:
        added_layer = None
        # Determine input tensors
        layer_input = [network_dict['new_output_tensor_of'][layer_aux]
                       for layer_aux in network_dict['input_layers_of'][layer.name]]

        if len(layer_input) == 1:
            layer_input = layer_input[0]

        # Insert layer if name matches the regular expression
        changed = False
        for layer_regex, new_layer in layer_regexs.items():
            if layer_regex != layer.name:
                continue

            changed = True
            x = new_layer(layer_input)
            print('Layer {} replace layer {}'.format(new_layer.name,
                                                     layer.name))
            added_layer = new_layer
            break

        if not changed:
            x = layer(layer_input)
            added_layer = layer

        conenctions[added_layer.name] = (added_layer.__class__,
                                         added_layer.get_config(),
                                         added_layer.get_weights())
        layers_order.append(added_layer.name)
        network_dict['new_output_tensor_of'].update({layer.name: x})


     # reconstruct graph
    tf.reset_default_graph()
    new_sess = tf.Session()
    tf.keras.backend.set_session(new_sess)

    input_constructor, input_conf, _ = conenctions[layers_order[0]]
    new_model_input = input_constructor.from_config(input_conf)
    network_dict['new_output_tensor_of'] = {new_model_input.name: new_model_input.input}

    for layer_name in layers_order[1:]:
        # Determine input tensors
        l_constuctor, l_conf, l_weights = conenctions[layer_name]
        layer = l_constuctor.from_config(l_conf)

        layer_input = [network_dict['new_output_tensor_of'][layer_aux]
                       for layer_aux in network_dict['input_layers_of'][layer_name]]
        if len(layer_input) == 1:
            layer_input = layer_input[0]
        x = layer(layer_input)
        layer.set_weights(l_weights)

        network_dict['new_output_tensor_of'].update({layer.name: x})

    new_model = Model(inputs=new_model_input.input, outputs=x)
    current_session.close()

    return new_model


def get_compressed_model(model,
                         decompose_info,
                         optimize_rank=False,
                         vbmf=True,
                         vbmf_weaken_factor=0.8):
    new_model = model
    changed = False

    layer_regexs = dict()
    for idx, layer in enumerate(model.layers[1:]):
        if layer.name not in decompose_info:
            continue

        decompose, decomp_rank = decompose_info[layer.name]

        if decompose.lower() == 'svd':
            layer_regexs[layer.name] = get_svd_seq(layer, rank=decomp_rank)
        elif decompose.lower() == 'cp3':
            layer_regexs[layer.name] = get_cp3_seq(layer,
                                                   rank=decomp_rank,
                                                   optimize_rank=optimize_rank)
        elif decompose.lower() == 'cp4':
            layer_regexs[layer.name] = get_cp4_seq(layer,
                                                   rank=decomp_rank,
                                                   optimize_rank=optimize_rank)
        elif decompose.lower() == 'tucker2':
            layer_regexs[layer.name] = get_tucker2_seq(layer,
                                                       rank=decomp_rank,
                                                       optimize_rank=optimize_rank,
                                                       vbmf=vbmf,
                                                       vbmf_weaken_factor=vbmf_weaken_factor)

    new_model = insert_layer_nonseq(new_model, layer_regexs)

    return new_model
