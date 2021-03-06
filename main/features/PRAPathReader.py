import os
import glob
import collections
import json
import shutil
import gzip
from subprocess import check_call
import numpy as np

# Important: PRA paths do not include entities. We need to follow the sequence of relations in a path to infer entities
#            in the path.


class PRAPathReader:
    def __init__(self, save_dir, include_entity):
        self.save_dir = save_dir
        self.max_length = None
        self.include_entity = include_entity
        self.include_path_len1 = None
        self.ignore_no_path_entity_pair = None
        self.read_params()

        self.relation_to_pairs_to_paths = {}
        # {rel: set(path_types)}
        self.relation_to_path_types = {}

    def read_params(self):
        with open(os.path.join(self.save_dir, "params.json")) as fh:
            params = json.load(fh)
            self.max_length = params['operation']['features']['path finder']["path finding iterations"] * 2
            # Important: Random walk can only generated paths without entities. Entities in a path need to be inferred.
            # Important: PRA generated paths always contain path length 1
            self.include_path_len1 = True
            self.ignore_no_path_entity_pair = True

    def read_paths(self, split):
        """
        This method takes in a split and read paths generated by PRA main. This method uses the split to ensure the
        split of the paths is the same as the split of the input.
        :param split:
        :return:
        """
        create_development_paths = False

        # check if paths for all relations exist
        relations = set()
        for rel in os.listdir(self.save_dir):
            if os.path.isdir(os.path.join(self.save_dir, rel)):
                relations.add(rel)
        for rel in split.relation_to_splits_to_instances:
            assert rel in relations

        for rel in split.relation_to_splits_to_instances:
            self.relation_to_path_types[rel] = set()
            self.relation_to_pairs_to_paths[rel] = {}
            rel_dir = os.path.join(self.save_dir, rel)

            if "development" in split.relation_to_splits_to_instances[rel]:
                split_filename = os.path.join(rel_dir, "development" + "_matrix.tsv")
                if not os.path.exists(split_filename):
                    create_development_paths = True

            for spt in split.relation_to_splits_to_instances[rel]:
                print("Extract", spt, "paths for relation:", rel)
                split_filename = os.path.join(rel_dir, spt + "_matrix.tsv")

                # Important: because paths extracted by PRA only only have train/test, we need to replace current
                #            train/test paths with train/dev/test paths, according to split, if dev exists in split.
                if spt == "development":
                    if create_development_paths:
                        continue

                assert os.path.exists(split_filename)

                with open(split_filename) as fh:
                    for line in fh:
                        content = line.strip().split("\t")
                        if len(content) == 3:
                            pair, label, paths = content
                        # Important: PRA doesn't include an entity pair without paths even if it is in the split.
                        #            We do. line below is for reading paths extracted by this main.
                        else:
                            raise Exception("Not enough values to unpack")
                        subj, obj = pair.split(",")
                        label = int(label)

                        # check
                        if (subj, obj, label) not in split.relation_to_splits_to_instances[rel][spt]:
                            if not create_development_paths:
                                raise Exception((subj, obj, label), "is not in original split")
                            else:
                                if (subj, obj, label) not in split.relation_to_splits_to_instances[rel]["development"]:
                                    raise Exception((subj, obj, label), "is not in original split")

                        if paths != "":
                            new_paths = set()
                            paths = paths.strip().split("-#-")
                            for path in paths:
                                # Paths generated by PRA main may be followed by random walk probabilities. We choose
                                # to ignore the probabilities.
                                edges = path.strip().split("-")[1:-1]
                                new_path = "-".join(edges)
                                new_paths.add(new_path)

                            # Important: paths between two entities may not be the same if we include path length 1
                            #            PRA generated paths because they contain length one path.

                            self.relation_to_pairs_to_paths[rel][(subj, obj)] = new_paths
                            self.relation_to_path_types[rel].update(new_paths)

        if create_development_paths:
            print("Split train paths into train/dev paths according to split")
            print("Current paths in folder will be replaced with paths with train/dev/test split")
            for rel in split.relation_to_splits_to_instances:
                rel_dir = os.path.join(self.save_dir, rel)
                if rel_dir[-1] == "/":
                    rel_dir = rel_dir[:-1]
                tmp_rel_dir = rel_dir + "_TMP"
                assert not os.path.exists(tmp_rel_dir)
                os.mkdir(tmp_rel_dir)

                for spt in split.relation_to_splits_to_instances[rel]:
                    spt_filename = os.path.join(tmp_rel_dir, spt + "_matrix.tsv")
                    with open(spt_filename, "w+") as fh:
                        for subj, obj, label in split.relation_to_splits_to_instances[rel][spt]:
                            if (subj, obj) in self.relation_to_pairs_to_paths[rel]:
                                paths = self.relation_to_pairs_to_paths[rel][(subj, obj)]
                                pra_paths = ["-" + path + "-,1.0" for path in paths]
                                paths_string = " -#- ".join(pra_paths)
                                fh.write(subj + "," + obj + "\t" + str(label) + "\t" + paths_string + "\n")

                # replace split_dir with new contents
                shutil.rmtree(rel_dir)
                shutil.copytree(tmp_rel_dir, rel_dir)
                shutil.rmtree(tmp_rel_dir)

    # PRA paths can be used to evaluate CVSM
    def write_cvsm_files(self, cvsm_dir, split, vocabs):
        if os.path.exists(cvsm_dir):
            shutil.rmtree(cvsm_dir)
        if not os.path.exists(cvsm_dir):
            os.mkdir(cvsm_dir)
        vocab_dir = os.path.join(cvsm_dir, "vocab")
        data_dir = os.path.join(cvsm_dir, "data_input")
        if not os.path.exists(vocab_dir):
            os.mkdir(vocab_dir)
        if not os.path.exists(data_dir):
            os.mkdir(data_dir)

        # Important: CVSM expects vocab files to end with .gz
        # 1. domain-label file. CVSM uses 0 and 1. We uses -1 and 1.
        print("Write domain label file")
        domain_label = {"domain":{"1":1, "-1":0}, "name":"label"}
        domain_label_filename = os.path.join(vocab_dir, "domain-label")
        with open(domain_label_filename, "w+") as fh:
            json.dump(domain_label, fh)

        # 2. relation_vocab file
        print("Write relation vocab file")
        relation_vocab_filename = os.path.join(vocab_dir, "relation_vocab.txt")
        # we can use vocabs.relation_to_idx, but we need to add #PAD_TOKEN to the dictionary
        relation_vocab = vocabs.relation_to_idx.copy()
        relation_vocab["#PAD_TOKEN"] = len(relation_vocab)
        with open(relation_vocab_filename, "w+") as fh:
            json.dump(relation_vocab, fh)

        # 3. create positive_matrix.tsv.translated, negative_matrix.tsv.translated, dev_matrix.tsv.translated,
        #    test_matrix.tsv.translated for each relation
        for rel in split.relation_to_splits_to_instances:
            print("Write data for", rel)
            rel_dir = os.path.join(data_dir, rel)
            os.mkdir(rel_dir)

            # 1. create training files
            spt = "training"
            positive_filename = os.path.join(rel_dir, "positive_matrix.tsv.translated")
            negative_filename = os.path.join(rel_dir, "negative_matrix.tsv.translated")

            # Important: entity pairs without paths will not be added because CVSM doesn't take entity pairs without
            #            paths.
            with open(positive_filename, "w+") as fhp:
                with open(negative_filename, "w+") as fhn:
                    for subj, obj, label in split.relation_to_splits_to_instances[rel][spt]:
                        if label == 1:
                            fh = fhp
                        elif label == -1:
                            fh = fhn
                        else:
                            raise Exception(label, "label is not recognized")
                        if (subj, obj) in self.relation_to_pairs_to_paths[rel]:
                            paths = self.relation_to_pairs_to_paths[rel][(subj, obj)]
                            assert paths
                            paths_str = "###".join(list(paths))
                            fh.write(subj + "\t" + obj + "\t" + paths_str + "\n")

            # 2. create test file
            spt = "testing"
            test_file = os.path.join(rel_dir, "test_matrix.tsv.translated")
            with open(test_file, "w+") as fh:
                for subj, obj, label in split.relation_to_splits_to_instances[rel][spt]:
                    assert label == 1 or label == -1
                    if (subj, obj) in self.relation_to_pairs_to_paths[rel]:
                        paths = self.relation_to_pairs_to_paths[rel][(subj, obj)]
                        assert paths
                        paths_str = "###".join(list(paths))
                        fh.write(subj + "\t" + obj + "\t" + paths_str + "\t" + str(label) + "\n")

            # 3. create empty dev file
            spt = "development"
            dev_file = os.path.join(rel_dir, "dev_matrix.tsv.translated")

            # if development split does not exist, create empty dev_file
            if spt not in split.relation_to_splits_to_instances[rel]:
                with open(dev_file, "w+") as fh:
                    pass
            else:
                with open(dev_file, "w+") as fh:
                    for subj, obj, label in split.relation_to_splits_to_instances[rel][spt]:
                        assert label == 1 or label == -1
                        if (subj, obj) in self.relation_to_pairs_to_paths[rel]:
                            paths = self.relation_to_pairs_to_paths[rel][(subj, obj)]
                            assert paths
                            paths_str = "###".join(list(paths))
                            fh.write(subj + "\t" + obj + "\t" + paths_str + "\t" + str(label) + "\n")

    # Important: slow and too many paths will be created. Do Not Use This Function.
    def infer_entities(self, vocabs, graph):
        """
        This method takes in pra paths and the graph to fill in entities in pra paths.
        :param vocabs:
        :param graph:
        :return:
        """
        sp_infer_entities(self.relation_to_pairs_to_paths, vocabs, graph)


def sp_infer_entities(relation_to_pairs_to_paths, vocabs, graph):
    for rel in relation_to_pairs_to_paths:
        for pair in relation_to_pairs_to_paths[rel]:
            for path in relation_to_pairs_to_paths[rel][pair]:
                edges = path.split("-")
                paths_with_entities = sp_follow_seq_edges(pair[0], pair[1], edges, vocabs, graph)
                print(path, "infer", len(paths_with_entities), "paths with entities")
                print(paths_with_entities)


def sp_follow_seq_edges(source, target, edges, vocabs, graph):
    """
    This function follows a sequence of edges to find paths including entities.
    source, target, and edges are all names instead of idx.
    :param source:
    :param target:
    :param edges:
    :return:
    """
    source_idx = vocabs.node_to_idx[source]
    target_idx = vocabs.node_to_idx[target]

    paths_with_entities = []
    # double ended queue. use append() and popleft() for FIFO.
    queue = collections.deque()
    queue.append((source_idx, tuple([source_idx]), 0))
    while queue:
        cur_node, path_so_far, path_position = queue.popleft()
        # print("current node", vocabs.idx_to_node[cur_node])

        if path_position == len(edges):
            if cur_node == target_idx:
                paths_with_entities.append(path_so_far)
        if path_position < len(edges):
            rel = edges[path_position]
            rel_idx = vocabs.relation_to_idx[rel]

            next_nodes = []
            if rel[0] != "_":
                if cur_node in graph.node_to_children:
                    children = graph.node_to_children[cur_node]
                    for child in children:
                        if rel_idx in graph.pair_to_relations[(cur_node, child)]:
                            next_nodes.append(child)
            else:
                if cur_node in graph.node_to_parents:
                    parents = graph.node_to_parents[cur_node]
                    for parent in parents:
                        if rel_idx in graph.pair_to_relations[(cur_node, parent)]:
                            next_nodes.append(parent)

            for next_node in next_nodes:
                # we don't allow self loop
                if next_node in path_so_far[::2]:
                    continue
                queue.append((next_node, path_so_far + (rel_idx, next_node), path_position+1))

    paths = []
    for path in paths_with_entities:
        path_str = ""
        for idx in range(0, int((len(path)-1)/2)):
            path_str += vocabs.idx_to_node[path[idx * 2]]
            path_str += "-" + vocabs.idx_to_relation[path[idx * 2 + 1]] + "-"
            if idx == (len(path) - 1) / 2 - 1:
                path_str += vocabs.idx_to_node[path[idx * 2 + 2]]
        paths.append(path_str)

    return paths