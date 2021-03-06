import os
import glob
import collections
import json
import shutil


class PathReader:
    def __init__(self, save_dir):
        self.save_dir = save_dir
        self.max_length = None
        self.include_entity = None
        self.multiple_instances_per_pair = None
        self.include_path_len1 = None
        self.ignore_no_path_entity_pair = None
        self.read_params()

        self.relation_to_pairs_to_paths = {}
        # {rel: set(path_types)}
        self.relation_to_path_types = {}

    def read_params(self):
        with open(os.path.join(self.save_dir, "params.json")) as fh:
            params = json.load(fh)
            if "simple" in params:
                self.max_length = params["max_length"]
                self.include_entity = params["include_entity"]
                self.include_path_len1 = params["include_path_len1"]
                self.ignore_no_path_entity_pair = params["ignore_no_path_entity_pair"]
                self.multiple_instances_per_pair = params["multiple_instances_per_pair"]
            else:
                self.max_length = params['operation']['features']['path finder']["number of steps"] * 2
                self.include_entity = False
                if "path type factory" in params['operation']['features']['path finder']:
                    if params['operation']['features']['path finder']["path type factory"] == "LexicalizedPathTypeFactory":
                        self.include_entity = True
                # Important: PRA generated paths always contain path length 1
                self.include_path_len1 = True
                self.ignore_no_path_entity_pair = True
                self.multiple_instances_per_pair = False

    def read_paths(self, split):
        """
        This method takes in a split and read paths generated by PRA main. This method uses the split to ensure the
        split of the paths is the same as the split of the input.
        :param split:
        :return:
        """
        relations_to_run = set()
        for rel in os.listdir(self.save_dir):
            if os.path.isdir(os.path.join(self.save_dir, rel)):
                relations_to_run.add(rel)
        assert set(split.relation_to_splits_to_instances.keys()) == relations_to_run

        path_numbers = []
        path_lengths = []
        for rel in split.relation_to_splits_to_instances:
            self.relation_to_path_types[rel] = set()
            self.relation_to_pairs_to_paths[rel] = {}
            rel_dir = os.path.join(self.save_dir, rel)

            for spt in split.relation_to_splits_to_instances[rel]:
                print("\nReading", spt, "paths for relation:", rel)
                split_filename = os.path.join(rel_dir, spt + "_matrix.tsv")
                assert os.path.exists(split_filename)

                # statistics
                num_instances = 0
                num_misses = 0
                with open(split_filename) as fh:
                    for line in fh:
                        content = line.strip().split("\t")
                        if len(content) == 3:
                            pair, label, paths = content
                        # Important: PRA doesn't include an entity pair without paths even if it is in the split.
                        #            We do. line below is for reading paths extracted by this main.
                        elif len(content) == 2:
                            pair, label = content
                            paths = ""
                        else:
                            raise Exception("Not enough values to unpack")
                        subj, obj = pair.split(",")
                        label = int(label)
                        if (subj, obj, label) not in split.relation_to_splits_to_instances[rel][spt]:
                            raise Exception((subj, obj, label), "is not in original split")

                        num_instances += 1
                        if paths != "":
                            new_paths = set()
                            paths = paths.strip().split("-#-")
                            for path in paths:
                                # Paths generated by PRA main may be followed by random walk probabilities. We choose
                                # to ignore the probabilities.
                                if "-" == path[0]:
                                    # from PRA main
                                    edges = path.strip().split("-")[1:-1]
                                else:
                                    # from this main
                                    edges = path.strip().split("-")

                                path_lengths.append((len(edges) - 1)/2)

                                new_path = "-".join(edges)
                                new_paths.add(new_path)

                            # Important: paths between two entities may not be the same if we include path length 1
                            #            PRA generated paths because they contain length one path.
                            path_numbers.append(len(new_paths))

                            if not self.multiple_instances_per_pair:
                                self.relation_to_pairs_to_paths[rel][(subj, obj)] = new_paths
                            else:
                                if (subj, obj) not in self.relation_to_pairs_to_paths[rel]:
                                    self.relation_to_pairs_to_paths[rel][(subj, obj)] = []
                                self.relation_to_pairs_to_paths[rel][(subj, obj)].append(new_paths)
                            self.relation_to_path_types[rel].update(new_paths)
                        else:
                            num_misses += 1
                print("Total of {} instances, {} instances without paths ({} percent)".format(num_instances, num_misses,
                                                                                      num_misses * 1.0 / num_instances))

        print("Avg # path per instance", sum(path_numbers) / len(path_numbers))
        print("Avg path length", sum(path_lengths) / len(path_lengths))

    # Paths can be used to evaluate cvsm
    # Debug: Experimental, only support input paths with entities. This may cause paths with entities that are different
    #        to be the same after removing entities. However, this should be fine since the original CVSM code also
    #        remove entities in paths that originally have entities.
    def write_cvsm_files(self, cvsm_dir, split, vocabs, entity2types_filename):
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

        ####################################################################
        # 1. vocabs
        # Important: CVSM expects vocab files to end with .gz
        # 1.1. domain-label file. CVSM uses 0 and 1. We uses -1 and 1.
        print("Write domain label file")
        domain_label = {"domain":{"1":1, "-1":0}, "name":"label"}
        domain_label_filename = os.path.join(vocab_dir, "domain-label")
        with open(domain_label_filename, "w+") as fh:
            json.dump(domain_label, fh)

        # 1.2. relation_vocab.txt file
        print("Write relation vocab file")
        relation_vocab_filename = os.path.join(vocab_dir, "relation_vocab.txt")
        # we can use vocabs.relation_to_idx, but we need to add #PAD_TOKEN to the dictionary
        relation_vocab = vocabs.relation_to_idx.copy()
        relation_vocab["#PAD_TOKEN"] = len(relation_vocab)
        relation_vocab["#END_RELATION"] = len(relation_vocab)
        with open(relation_vocab_filename, "w+") as fh:
            json.dump(relation_vocab, fh)

        # 1.3. entity_vocab.txt file
        print("Write entity vocab file")
        entity_vocab_filename = os.path.join(vocab_dir, "entity_vocab.txt")
        # we can use vocabs.relation_to_idx, but we need to add #PAD_TOKEN to the dictionary
        entity_vocab = vocabs.node_to_idx.copy()
        entity_vocab["#PAD_TOKEN"] = len(entity_vocab)
        with open(entity_vocab_filename, "w+") as fh:
            json.dump(entity_vocab, fh)

        # 1.4. entity_to_list_type.json file and entity_type_vocab.txt
        # Important: entity2types are mapping entities with no type information (e.g., lid.n.02 instead of
        #            object:lid.n.02) to type hiearchies. entity_to_list_type needs to be a map from entities with type
        #            information to type hierarchies.
        print("Writing entity to types file and entity type vocab file")
        entity_to_list_type_filename = os.path.join(vocab_dir, "entity_to_list_type.json")
        entity_type_vocab_filename = os.path.join(vocab_dir, "entity_type_vocab.txt")

        # read entity2types
        with open(entity2types_filename, "r") as fh:
            entity2types = json.load(fh)
        # we are going to use entity2types to generate entity_type_vocab and entity_to_list_type
        entity_type_vocab = {}
        entity_to_list_type = {}
        for entity in entity2types:
            types = entity2types[entity]
            # a. construct type vocab
            for type in types:
                if type not in entity_type_vocab:
                    entity_type_vocab[type] = len(entity_type_vocab)

            # b. write entity to list of types
            # Important: Because entity2types contain maps from entity (not typed) to its type hierarchies, we need to
            #            find all typed entities that can use type hierarchies. For example, entity2types contain type
            #            hierarchies for bowl, we need to write the type hierarchies to object:bowl and location:bowl in
            #            entity_to_list_type.
            typed_entities = []
            for typed_entity in vocabs.node_to_idx:
                if ":".join(typed_entity.split(":")[1:]) == entity:
                    typed_entities.append(typed_entity)
            for typed_entity in typed_entities:
                entity_to_list_type[typed_entity] = types

        entity_type_vocab["#PAD_TOKEN"] = len(entity_type_vocab)
        with open(entity_type_vocab_filename, "w+") as fh:
            json.dump(entity_type_vocab, fh)
        with open(entity_to_list_type_filename, "w+") as fh:
            json.dump(entity_to_list_type, fh)

        ####################################################################
        # 2. Paths
        # create positive_matrix.tsv.translated, negative_matrix.tsv.translated, dev_matrix.tsv.translated,
        # and test_matrix.tsv.translated for each relation
        for rel in split.relation_to_splits_to_instances:
            print("Write data for", rel)
            rel_dir = os.path.join(data_dir, rel)
            os.mkdir(rel_dir)

            # 2.1 create training files
            spt = "training"
            positive_filename = os.path.join(rel_dir, "positive_matrix.tsv.translated")
            negative_filename = os.path.join(rel_dir, "negative_matrix.tsv.translated")

            # Important: entity pairs without paths will not be added because CVSM doesn't take them
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
                            if not self.multiple_instances_per_pair:
                                paths_list = [self.relation_to_pairs_to_paths[rel][(subj, obj)]]
                            else:
                                paths_list = self.relation_to_pairs_to_paths[rel][(subj, obj)]
                            for paths in paths_list:
                                # Important: CVSM takes paths without source and target if paths contain entities.
                                cutted_paths = []
                                for path in paths:
                                    cutted_path = "-".join(path.split("-")[1:-1])
                                    cutted_paths.append(cutted_path)
                                # ignore pairs without paths
                                if cutted_paths:
                                    paths_str = "###".join(list(cutted_paths))
                                    fh.write(subj + "\t" + obj + "\t" + paths_str + "\n")

            # 2.2 create test file
            spt = "testing"
            test_file = os.path.join(rel_dir, "test_matrix.tsv.translated")
            with open(test_file, "w+") as fh:
                for subj, obj, label in split.relation_to_splits_to_instances[rel][spt]:
                    assert label == 1 or label == -1
                    if (subj, obj) in self.relation_to_pairs_to_paths[rel]:
                        if not self.multiple_instances_per_pair:
                            paths_list = [self.relation_to_pairs_to_paths[rel][(subj, obj)]]
                        else:
                            paths_list = self.relation_to_pairs_to_paths[rel][(subj, obj)]
                        for paths in paths_list:
                            # Important: CVSM takes paths without source and target if paths contain entities.
                            cutted_paths = []
                            for path in paths:
                                cutted_path = "-".join(path.split("-")[1:-1])
                                cutted_paths.append(cutted_path)
                            # ignore pairs without paths
                            if cutted_paths:
                                paths_str = "###".join(list(cutted_paths))
                                fh.write(subj + "\t" + obj + "\t" + paths_str + "\t" + str(label) + "\n")

            # 2.3 create empty dev file
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
                            if not self.multiple_instances_per_pair:
                                paths_list = [self.relation_to_pairs_to_paths[rel][(subj, obj)]]
                            else:
                                paths_list = self.relation_to_pairs_to_paths[rel][(subj, obj)]
                            for paths in paths_list:
                                # Important: CVSM takes paths without source and target if paths contain entities.
                                cutted_paths = []
                                for path in paths:
                                    cutted_path = "-".join(path.split("-")[1:-1])
                                    cutted_paths.append(cutted_path)
                                # ignore pairs without paths
                                if cutted_paths:
                                    paths_str = "###".join(list(cutted_paths))
                                    fh.write(subj + "\t" + obj + "\t" + paths_str + "\t" + str(label) + "\n")


def compare_path_readers(path_reader1, path_reader2):
    print("Compare paths")
    pairs = set()
    pairs.update(path_reader1.pair_to_paths.keys())
    pairs.update(path_reader2.pair_to_paths.keys())
    print(len(path_reader1.pair_to_paths))
    print(len(path_reader2.pair_to_paths))
    for pair in pairs:
        if pair not in path_reader1.pair_to_paths:
            print(pair, "not in 1, 2 has", len(path_reader2.pair_to_paths[pair]))
        elif pair not in path_reader2.pair_to_paths:
            print(pair, "not in 2, 1 has", len(path_reader1.pair_to_paths[pair]))
        else:
            if len(path_reader1.pair_to_paths[pair]) != len(path_reader1.pair_to_paths[pair]):
                print(pair, len(path_reader1.pair_to_paths[pair]),
                      len(path_reader2.pair_to_paths[pair]))
                paths1 = path_reader1.pair_to_paths[pair]
                paths2 = path_reader2.pair_to_paths[pair]
                print(paths1, paths2)