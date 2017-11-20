import os
import sys
import glob
import h5py
import numpy as np

sys.path.append('..')
import lib
from lib.config import (config_output_dir, config_output_dataset_path,
                        config_mesh_dir, project_dir)
from lib import vrep
vrep.simxFinish(-1)

import simulator as SI


def load_subset(h5_file, object_keys, shuffle=True):
    """Loads a subset of an hdf5 file given top-level keys.

    The hdf5 file was created using object names as the top-level keys.
    Loading a subset of this file means loading data corresponding to
    specific objects.
    """

    if not isinstance(object_keys, list):
        object_keys = [object_keys]

    # Load the grasp properties.
    props = {'object_name': []}

    for obj in object_keys:

        data = h5_file[obj]['pregrasp']

        # Since there are multiple properties per grasp, we'll do something
        # ugly and store intermediate results into a list, and merge all the
        # properties after we've looped through all the objects.
        for p in data.keys():
            if p not in props:
                props[p] = []
            props[p].append(np.vstack(data[p][:]))

        name = np.atleast_2d([obj])
        name = np.repeat(name, len(data['frame_work2palm']), axis=0)
        props['object_name'].append(name)

    grasps = np.vstack(props['grasp'])
    del props['grasp']

    # Merge the list-of-lists for each property into single arrays
    idx = np.arange(grasps.shape[0])
    if shuffle is True:
        np.random.shuffle(idx)

    grasps = grasps[idx]
    for key in props.keys():
        props[key] = np.vstack(props[key])[idx]
    return grasps, props


def plot_queried_images(image1, image2, postfix_name):
    """Plots an image of [object, grasp on object] pairs."""

    from scipy import misc
    _, channels, num_rows, num_cols = image1.shape
    fig = np.zeros((num_rows, num_cols * 2, 3))
    fig[:, :num_cols] = image1[0, :3].transpose(1, 2, 0)
    fig[:, num_cols:] = image2[0, :3].transpose(1, 2, 0)

    save_dir = os.path.join(config_output_dir, 'grasp_images')
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)
    misc.imsave(os.path.join(save_dir, postfix_name), fig)


def is_valid_image(mask, num_pixel_thresh=400):
    """Checks the amount of object in the image & its within all bounds."""

    where_object = np.vstack(np.where(mask == 0)).T
    if len(where_object) == 0:
        return False

    min_row, min_col = np.min(where_object, axis=0)
    max_row, max_col = np.max(where_object, axis=0)

    if min_row == 0 or min_col == 0:
        return False
    elif max_row == mask.shape[0] - 1:
        return False
    elif max_col == mask.shape[1] - 1:
        return False
    return len(where_object) >= num_pixel_thresh


def get_minibatch(grasps, props, index, num_views, save_queried_images=True):
    """Queries simulator for an image and formats grasp to be WRT camera."""

    # Used to take a duplicate image of the current scene
    q_non_random = query_params.copy()
    q_non_random['randomize_lighting'] = False
    q_non_random['randomize_texture'] = False
    q_non_random['randomize_colour'] = False
    q_non_random['reorient_up'] = False

    # These properties don't matter too much since we're not going to be
    # dynamically simulating the object
    mass = props['mass_wrt_world'][index]
    com = props['com_wrt_world'][index]
    inertia = props['inertia_wrt_world'][index]

    frame_world2work = props['frame_world2work'][index]
    frame_work2obj = props['frame_work2obj'][index]
    frame_work2palm = props['frame_work2palm'][index]
    frame_work2palm = lib.utils.format_htmatrix(frame_work2palm)

    # TODO: A little hacky. Try to clean this up so the full path is specified,
    # or object with full extension is given in dataset.
    base_path = os.path.join(config_mesh_dir, props['object_name'][index, 0])
    object_path = str(glob.glob(base_path + '*')[0])

    sim.load_object(object_path, com, mass, inertia)

    sim.set_object_pose(frame_work2obj)

    # Collect images of the object by itself, and the pre-grasp that was used.
    # Set gripper pose & toggle between being visible / invisible across views
    sim.set_gripper_pose(frame_work2palm)
    for key in props.keys():
        if 'joint' not in key:
            continue
        pos = float(props[key][index, 0])
        sim.set_joint_position_by_name(str(key), pos)

    sim_images_reg, sim_images_grasp, sim_grasps, sim_work2cam = [], [], [], []

    # For each successful grasp, we'll do a few randomizations of camera / obj
    for count in xrange(num_views):

        # Toggle the gripper to be invisible / visible to the camera, to get
        # an image of the object with & without pregrasp pose
        sim.set_gripper_properties()

        while True:

            # We'll use the pose of where the grasp succeeded from as an
            # initial seedpoint for collecting images. For each image, we
            # slightly randomize the cameras pose, but make sure the camera is
            # always above the tabletop
            frame_work2cam = lib.utils.randomize_pose(frame_work2palm, **pose_params)

            if frame_work2cam[11] <= 0.2:
                continue

            # Take an image of the object
            image_wo_gripper, frame_work2cam_ht = \
                sim.query(frame_work2cam, frame_world2work, **query_params)

            if image_wo_gripper is None:
                raise Exception('No image returned.')
            elif not is_valid_image(image_wo_gripper[0, 4], num_pixel_thresh=600):
                continue

            # Take an image of the grasp that was used on the object
            sim.set_gripper_properties(visible=True, renderable=True)

            image_w_gripper, _ = sim.query(frame_work2cam_ht[:3].flatten(),
                                           frame_world2work, **q_non_random)

            frame_cam2work_ht = lib.utils.invert_htmatrix(frame_work2cam_ht)
            grasp = lib.utils.convert_grasp_frame(frame_cam2work_ht, grasps[index])

            sim_grasps.append(np.float32(grasp))
            sim_images_reg.append(np.float16(image_wo_gripper))
            sim_images_grasp.append(np.float16(image_w_gripper))
            sim_work2cam.append(np.float32(frame_work2cam_ht[:3].flatten()))

            if save_queried_images:
                name = str(props['object_name'][index, 0])
                name = '%d_%d_%s.png' % (index, count, name)
                plot_queried_images(image_wo_gripper, image_w_gripper, name)

            break

    return (np.vstack(sim_images_reg), np.vstack(sim_images_grasp),
            np.vstack(sim_grasps), np.vstack(sim_work2cam))


def create_dataset(inputs, input_props, num_views, dataset_name):
    """Collects images from sim & creates dataset for doing ML."""

    f = h5py.File(dataset_name, 'w')

    # Initialize some structures for holding the dataset
    res = query_params['resolution']

    im_shape = (inputs.shape[0] * num_views, 5, res, res)
    gr_shape = (inputs.shape[0] * num_views, inputs.shape[1])
    mx_shape = (inputs.shape[0] * num_views, 12)

    image_w_gripper = f.create_dataset('images_w_gripper', im_shape, dtype='float16')
    image_wo_gripper = f.create_dataset('images_wo_gripper', im_shape, dtype='float16')
    dset_grasp = f.create_dataset('grasps', gr_shape)

    group = f.create_group('props')
    for key in input_props.keys():
        num_var = input_props[key].shape[1]
        if key == 'object_name':
            dt = h5py.special_dtype(vlen=unicode)
            group.create_dataset(key, (inputs.shape[0] * num_views, 1), dtype=dt)
        else:
            group.create_dataset(key, (inputs.shape[0] * num_views, num_var))
    group.create_dataset('frame_work2cam', mx_shape)

    # Loop through our collected data, and query the sim for an image and
    # grasp transformed to the camera's frame
    save_indices = np.arange(len(inputs) * num_views)
    np.random.shuffle(save_indices)

    for idx in range(0, len(inputs)):

        print idx, ' / ', len(inputs)

        # Query simulator for data
        q_images_reg, q_images_grasp, q_grasps, q_work2cam = \
            get_minibatch(inputs, input_props, idx, num_views)

        # We save the queried information in a "shuffled" manner, so grasps
        # for the same object are spread throughout the dataset
        low = idx * num_views
        high = (idx + 1) * num_views

        for i, save_i in enumerate(save_indices[low:high]):
            image_w_gripper[save_i] = q_images_grasp[i]
            image_wo_gripper[save_i] = q_images_reg[i]
            dset_grasp[save_i] = q_grasps[i]

            for key in input_props.keys():
                group[key][save_i] = input_props[key][idx]
            group['frame_work2cam'][save_i] = q_work2cam[i]

    f.close()


if __name__ == '__main__':

    spawn_params = {'port': 19997,
                    'ip': '127.0.0.1',
                    'vrep_path': None,
                    'scene_path': None,
                    'exit_on_stop': True,
                    'spawn_headless': False,
                    'spawn_new_console': True}

    query_params = {'rgb_near_clip': 0.01,
                    'depth_near_clip': 0.01,
                    'rgb_far_clip': 10.,
                    'depth_far_clip': 1.25,
                    'camera_fov': 70 * np.pi / 180,
                    'resolution': 256,
                    'p_light_off': 0.25,
                    'p_light_mag': 0.1,
                    'reorient_up': True,
                    'randomize_texture': True,
                    'randomize_colour': True,
                    'randomize_lighting': True,
                    'texture_path': os.path.join(project_dir, 'texture.png')}

    pose_params = {'local_rot': (10, 10, 10),
                   'global_rot': (50, 50, 50),
                   'base_offset': -0.4,
                   'offset_mag': 0.4}

    # spawn_params['vrep_path'] = 'C:\\Program Files\\V-REP3\\V-REP_PRO_EDU\\vrep.exe'
    sim = SI.SimulatorInterface(**spawn_params)

    np.random.seed(1234)
    batch_views = 10
    max_samples = 50
    max_valid_samples = 10
    n_test_objects = 2
    shuffle_data = False

    f = h5py.File(config_output_dataset_path, 'r')

    grasps, props = load_subset(f, f.keys(), shuffle_data)
    create_dataset(grasps, props, batch_views, 'dataset.hdf5')
