import os

# import and build cython
import pyximport
pyximport.install()

from main.data.WordnetReader import WordnetReader
from main.data.TypedRelationInstances import TypedRelationInstances
from main.data.Vocabs import Vocabs
from main.data.Split import Split
from main.graphs.AdjacencyGraph import AdjacencyGraph
from main.features.PathExtractor import PathExtractor
from main.experiments.CVSMDriver import CVSMDriver
from main.experiments.PRADriver import PRADriver
from main.playground.make_data_format import process_paths
from main.playground.model2.CompositionalVectorAlgorithm import CompositionalVectorAlgorithm

# This script is used to run WNRR18 experiments (only our method) with 1:10 postive to negative ratio.

if __name__ == "__main__":

    # location of Das et al.'s repo
    CVSM_RUN_DIR = "/home/weiyu/Research/Path_Baselines/CVSM/ChainsofReasoning"
    # location of Matt's PRA scala repo
    PRA_RUN_DIR = "/home/weiyu/Research/Path_Baselines/SFE/pra_scala"

    DATASET_NAME = "wn18rr"
    DATASET_FOLDER = os.path.join("data", DATASET_NAME)
    PRA_TEMPLATE_DIR = "pra_templates"

    WORDNET_DIR = DATASET_FOLDER
    DOMAIN_FILENAME = os.path.join(DATASET_FOLDER, "domains.tsv")
    RANGE_FILENAME = os.path.join(DATASET_FOLDER, "ranges.tsv")
    EDGES_FILENAME = os.path.join(DATASET_FOLDER, "edges.txt")
    PRA_DIR = os.path.join(DATASET_FOLDER, "pra")
    SPLIT_DIR = os.path.join(DATASET_FOLDER, "split")
    CPR_PATH_DIR = os.path.join(DATASET_FOLDER, "cpr_paths")
    WORD2VEC_FILENAME = "data/word2vec/GoogleNews-vectors-negative300.bin"
    ENTITY2VEC_FILENAME = os.path.join(DATASET_FOLDER, "synonym2vec.pkl")

    CVSM_DATA_DIR = os.path.join(DATASET_FOLDER, "cvsm")
    PRA_PATH_DIR = os.path.join(DATASET_FOLDER, "pra_paths")
    RELATION_PATH_DIR = os.path.join(DATASET_FOLDER, "relation_paths")
    PATH_DIR = os.path.join(DATASET_FOLDER, "paths")
    NEW_PATH_DIR = os.path.join(DATASET_FOLDER, "new_paths")
    AUGMENT_PATH_DIR = os.path.join(DATASET_FOLDER, "paths_augment")

    CVSM_RET_DIR = os.path.join(DATASET_FOLDER, "cvsm_entity")
    ENTITY_TYPE2VEC_FILENAME = os.path.join(DATASET_FOLDER, "entity_type2vec.pkl")

    run_step = 1

    # 1. first run main/data/WordnetReader.py to process data and generate necessary files
    if run_step == 1:
        wn = WordnetReader(WORDNET_DIR,
                           filter=True,
                           word2vec_filename=WORD2VEC_FILENAME,
                           remove_repetitions=False)
        wn.read_data()
        wn.get_entity_types()
        wn.write_relation_domain_and_ranges()
        wn.write_edges()

    # 2. use PRA scala code and code here to create train/test/dev split and negative examples
    if run_step == 2:
        pra_driver = PRADriver(DATASET_FOLDER, PRA_TEMPLATE_DIR, PRA_RUN_DIR, DATASET_NAME)
        pra_driver.prepare_split()

    # 3. Extract paths with entities
    if run_step == 3:
        typed_relation_instances = TypedRelationInstances()
        typed_relation_instances.read_domains_and_ranges(DOMAIN_FILENAME, RANGE_FILENAME)
        typed_relation_instances.construct_from_labeled_edges(EDGES_FILENAME, entity_name_is_typed=False,
                                                              is_labeled=False)
        vocabs = Vocabs()
        vocabs.build_vocabs(typed_relation_instances)
        split = Split()
        split.read_splits(SPLIT_DIR, vocabs, entity_name_is_typed=True)
        graph = AdjacencyGraph()
        graph.build_graph(typed_relation_instances, vocabs)

        path_extractor = PathExtractor(max_length=6, include_entity=True, save_dir=PATH_DIR, include_path_len1=True,
                                       max_paths_per_pair=200, multiple_instances_per_pair=False,
                                       max_instances_per_pair=None, paths_sample_method="all_lengths")
        path_extractor.extract_paths(graph, split, vocabs)
        path_extractor.write_paths(split)

    # 4. Process data for running the model
    if run_step == 4:
        # first convert paths to cvsm format
        cvsm_driver = CVSMDriver(WORDNET_DIR, CVSM_RUN_DIR, dataset="wordnet", include_entity=True, has_entity=True,
                                 augment_data=False, include_entity_type=True)
        cvsm_driver.setup_cvsm_dir()

        # then vectorize cvsm format data
        process_paths(input_dir=os.path.join(CVSM_RET_DIR, "data/data_input"),
                      output_dir=os.path.join(CVSM_RET_DIR, "data/data_output"),
                      vocab_dir=os.path.join(CVSM_RET_DIR, "data/vocab"),
                      isOnlyRelation=False,
                      getOnlyRelation=False,
                      MAX_POSSIBLE_LENGTH_PATH=8,  # the max number of relations in a path + 1
                      NUM_ENTITY_TYPES_SLOTS=15,  # the number of types + 1 (the reason we +1 is to create a meaningless type for all entities)
                      pre_padding=True)

    # 5. Run the model
    # use $tensorboard --logdir runs to see the training progress
    if run_step == 5:
        cvsm = CompositionalVectorAlgorithm("wordnet", CVSM_RET_DIR, ENTITY_TYPE2VEC_FILENAME)

        # Not using pretrained word embeddings decreases performance
        # cvsm = CompositionalVectorAlgorithm("wordnet", CVSM_RET_DIR, None)

        cvsm.train_and_test()

        # Uncomment if need to train only one relation
        # cvsm.train("/home/weiyu/Research/ChainsOfReasoningWithAbstractEntities/data/wn18rr/cvsm_entity/data/data_output/member_of_domain_region")

    if run_step == 6:
        cvsm = CompositionalVectorAlgorithm("wordnet", CVSM_RET_DIR, ENTITY_TYPE2VEC_FILENAME,
                                            pooling_method="sat", attention_method="sat", early_stopping_metric="map",
                                            visualize=True, calculate_path_attn_stats=True, calculate_type_attn_stats=True,
                                            best_models={'verb_group': {'val_acc': 1.0, 'val_ap': 1.0, 'epoch': 0, 'test_ap': 1.0, 'test_acc': 1.0}, 'member_meronym': {'val_acc': 0.9431578947368421, 'val_ap': 0.7135667457942702, 'epoch': 19, 'test_ap': 0.6335514032344876, 'test_acc': 0.9408812046848857}, 'hypernym': {'val_acc': 0.989671984536826, 'val_ap': 0.9642082965792328, 'epoch': 16, 'test_ap': 0.9620883932417185, 'test_acc': 0.988660197755088}, 'also_see': {'val_acc': 0.9683306494900698, 'val_ap': 0.9301494111857955, 'epoch': 7, 'test_ap': 0.904053400950515, 'test_acc': 0.9729148753224419}, 'similar_to': {'val_acc': 0.9795918367346939, 'val_ap': 1.0, 'epoch': 3, 'test_ap': 1.0, 'test_acc': 0.9844961240310077}, 'member_of_domain_region': {'val_acc': 0.9590865842055185, 'val_ap': 0.7790840930128, 'epoch': 13, 'test_ap': 0.6968178289261723, 'test_acc': 0.954858454475899}, 'instance_hypernym': {'val_acc': 0.9630209965528047, 'val_ap': 0.8795758247163307, 'epoch': 8, 'test_ap': 0.8778889539424873, 'test_acc': 0.9612403100775194}, 'synset_domain_topic_of': {'val_acc': 0.9447174447174447, 'val_ap': 0.7312231001822086, 'epoch': 13, 'test_ap': 0.7427533669498867, 'test_acc': 0.9436564223798266}, 'derivationally_related_form': {'val_acc': 1.0, 'val_ap': 1.0, 'epoch': 0, 'test_ap': 1.0, 'test_acc': 1.0}, 'has_part': {'val_acc': 0.9431347849559114, 'val_ap': 0.7213513082273592, 'epoch': 13, 'test_ap': 0.6589302705002684, 'test_acc': 0.9376080691642651}, 'member_of_domain_usage': {'val_acc': 0.9627403846153846, 'val_ap': 0.9055411128578176, 'epoch': 6, 'test_ap': 0.8644352979656229, 'test_acc': 0.957487922705314}})
        cvsm.train_and_test()