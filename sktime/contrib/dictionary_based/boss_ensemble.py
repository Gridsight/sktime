import numpy as np
import random
import sys
import pandas as pd
from sklearn.base import BaseEstimator
from sklearn.utils.multiclass import class_distribution

from sktime.distances.dictionary_distances import boss_distance
from sktime.transformers.SFA import SFA

all__ = ["BOSSEnsemble", "BOSSIndividual"]


class BOSSEnsemble(BaseEstimator):
    __author__ = "Matthew Middlehurst"

    """ Bag of SFA Symbols (BOSS)

    Bag of SFA Symbols Ensemble: implementation of BOSS from Schaffer :
    @article
    {schafer15boss,
     author = {Patrick Schäfer,
            title = {The BOSS is concerned with time series classification in the presence of noise},
            journal = {Data Mining and Knowledge Discovery},
            volume = {29},
            number= {6},
            year = {2015}
    }
    Overview: Input n series length m
    BOSS performs a gird search over a set of parameter values, evaluating each with a LOOCV. If then retains
    all ensemble members within 92% of the best. There are three primary parameters: 
            alpha: alphabet size
            w: window length
            l: word length.
    for any combination, a single BOSS slides a window length w along the series. The w length window is shortened to 
    an l length word through taking a Fourier transform and keeping the first l/2 complex coefficients. These l 
    coefficents are then discretised into alpha possible values, to form a word length l. A histogram of words for each 
    series is formed and stored. fit involves finding n histograms. 
    
    predict uses 1 nearest neighbour with a bespoke distance function.  
    
    For the Java version, see
    https://github.com/TonyBagnall/uea-tsc/blob/master/src/main/java/timeseriesweka/classifiers/BOSS.java


    Parameters
    ----------
    randomised_ensemble   : bool, turns the option to just randomise the ensemble members rather than cross validate (default=False) 
    random_ensemble_size: int, if randomising, generate this number of base classifiers
    random_state    : int or None, seed for random, integer, optional (default to no seed)
    dim_to_use      : int >=0, the column of the panda passed to use, optional (default = 0)
    threshold       : double [0,1]. retain all classifiers within threshold% of the best one, optional (default =0.92)
    max_ensemble_size    : int, retain a maximum number of classifiers, even if within threshold, optional (default = 500)
    wordLengths     : list of int, search space for word lengths (default =100)
    alphabet_size    : range of alphabet sizes to try (default to single value, 4)
    
    Attributes
    ----------
    num_classes    : extracted from the data
    num_atts       : extracted from the data
    classifiers    : array of DecisionTree classifiers
    intervals      : stores indexes of the start and end points for all classifiers
    dim_to_use     : the column of the panda passed to use (can be passed a multidimensional problem, but will only use one)

    """

    def __init__(self,
                 randomised_ensemble=False,
                 ensemble_size=100,
                 random_state=None,
                 dim_to_use=0,
                 threshold=0.92,
                 max_ensemble_size=250,
                 word_lengths=None,
                 alphabet_size=4,
                 min_window=10,
                 norm_options=None
                 ):
        if word_lengths is None:
            word_lengths = [16, 14, 12, 10, 8]
        if norm_options is None:
            norm_options = [True, False]

        self.randomised_ensemble = randomised_ensemble
        self.ensemble_size = ensemble_size
        self.random_state = random_state
        random.seed(random_state)
        self.dim_to_use = dim_to_use
        self.threshold = threshold
        self.max_ensemble_size = max_ensemble_size

        self.seed = 0
        self.classifiers = []
        self.num_classes = 0
        self.classes_ = []
        self.class_dictionary = {}
        self.num_classifiers = 0
        self.series_length = 0

        # For the multivariate case treating this as a univariate classifier

        # Parameter search values
        self.word_lengths = word_lengths
        self.norm_options = norm_options
        self.alphabet_size = alphabet_size
        self.min_window = min_window

    def fit(self, X, y):
        """Build an ensemble of BOSS classifiers from the training set (X, y), either through randomising over the para
         space to make a fixed size ensemble quickly or by creating a variable size ensemble of those within a threshold
         of the best
        Parameters
        ----------
        X : array-like or sparse matrix of shape = [n_samps, num_atts]
            The training input samples.  If a Pandas data frame is passed, the column _dim_to_use is extracted
        y : array-like, shape = [n_samples] or [n_samples, n_outputs]
            The class labels.

        Returns
        -------
        self : object
         """

        if isinstance(X, pd.DataFrame):
            if isinstance(X.iloc[0, self.dim_to_use], pd.Series):
                X = np.asarray([a.values for a in X.iloc[:, 0]])
            else:
                raise TypeError("Input should either be a 2d numpy array, or a pandas dataframe containing "
                                "Series objects")

        num_insts, self.series_length = X.shape
        self.num_classes = np.unique(y).shape[0]
        self.classes_ = class_distribution(np.asarray(y).reshape(-1, 1))[0][0]
        for index, classVal in enumerate(self.classes_):
            self.class_dictionary[classVal] = index

        # Window length parameter space dependent on series length

        max_window_searches = self.series_length / 4
        win_inc = int((self.series_length - self.min_window) / max_window_searches)
        if win_inc < 1:
            win_inc = 1

        if self.randomised_ensemble:
            random.seed(self.seed)

            while len(self.classifiers) < self.ensemble_size:
                word_len = self.word_lengths[random.randint(0, len(self.word_lengths) - 1)]
                win_size = self.min_window + win_inc * random.randint(0, max_window_searches)
                if win_size > max_window_searches:
                    win_size = max_window_searches
                normalise = random.random() > 0.5

                boss = BOSSIndividual(win_size, self.word_lengths[word_len], self.alphabet_size, normalise)
                boss.fit(X, y)
                boss.clean()
                self.classifiers.append(boss)
        else:
            max_acc = -1
            min_max_acc = -1

            for i, normalise in enumerate(self.norm_options):
                for win_size in range(self.min_window, self.series_length + 1, win_inc):
                    boss = BOSSIndividual(win_size, self.word_lengths[0], self.alphabet_size, normalise)
                    boss.fit(X, y)

                    best_classifier_for_win_size = boss
                    best_acc_for_win_size = -1
                    best_word_len = self.word_lengths[0]

                    for n, word_len in enumerate(self.word_lengths):
                        if n > 0:
                            boss = boss.shorten_bags(word_len)

                        correct = 0
                        for g in range(num_insts):
                            c = boss.train_predict(g)
                            if c == y[g]:
                                correct += 1

                        accuracy = correct / num_insts
                        if accuracy >= best_acc_for_win_size:
                            best_acc_for_win_size = accuracy
                            best_classifier_for_win_size = boss
                            best_word_len = word_len

                    if self.include_in_ensemble(best_acc_for_win_size, max_acc, min_max_acc, len(self.classifiers)):
                        best_classifier_for_win_size.clean()
                        best_classifier_for_win_size.set_word_len(best_word_len)
                        best_classifier_for_win_size.accuracy = best_acc_for_win_size
                        self.classifiers.append(best_classifier_for_win_size)

                        if best_acc_for_win_size > max_acc:
                            max_acc = best_acc_for_win_size

                            for c, classifier in enumerate(self.classifiers):
                                if classifier.accuracy < max_acc * self.threshold:
                                    self.classifiers.remove(classifier)

                        min_max_acc, min_acc_ind = self.worst_of_best()

                        if len(self.classifiers) > self.max_ensemble_size:
                            del self.classifiers[min_acc_ind]
                            min_max_acc, min_acc_ind = self.worst_of_best()

        self.num_classifiers = len(self.classifiers)

    def predict(self, X):
        return [self.classes_[int(np.argmax(prob))] for prob in self.predict_proba(X)]

    def predict_proba(self, X):
        if isinstance(X, pd.DataFrame):
            if isinstance(X.iloc[0, self.dim_to_use], pd.Series):
                X = np.asarray([a.values for a in X.iloc[:, 0]])
            else:
                raise TypeError("Input should either be a 2d numpy array, or a pandas dataframe containing "
                                "Series objects")

        sums = np.zeros((X.shape[0], self.num_classes))

        for n, clf in enumerate(self.classifiers):
            preds = clf.predict(X)
            for i in range(0, X.shape[0]):
                sums[i, self.class_dictionary.get(preds[i])] += 1

        dists = sums / (np.ones(self.num_classes) * self.num_classifiers)

        return dists

    def include_in_ensemble(self, acc, max_acc, min_max_acc, size):
        if acc >= max_acc * self.threshold:
            if size >= self.max_ensemble_size:
                return acc > min_max_acc
            else:
                return True
        return False

    def worst_of_best(self):
        min_acc = -1
        min_acc_idx = 0

        for c, classifier in enumerate(self.classifiers):
            if classifier.accuracy < min_acc:
                min_acc = classifier.accuracy
                min_acc_idx = c

        return min_acc, min_acc_idx

    def get_train_probs(self, X):
        num_inst = X.shape[0]
        results = np.zeros((num_inst, self.num_classes))
        divisor = (np.ones(self.num_classes) * self.num_classifiers)
        for i in range(num_inst):
            sums = np.zeros(self.num_classes)

            for n in range(len(self.classifiers)):
                sums[self.class_dictionary.get(self.classifiers[n].train_predict(i), -1)] += 1

            dists = sums / divisor
            for n in range(self.num_classes):
                results[i][n] = dists[n]
        return results

    def find_ensemble_train_acc(self, X, y):
        num_inst = X.shape[0]
        results = np.zeros((2 + self.num_classes, num_inst + 1))
        correct = 0

        for i in range(num_inst):
            sums = np.zeros(self.num_classes)

            for n in range(len(self.classifiers)):
                sums[self.class_dictionary.get(self.classifiers[n].train_predict(i), -1)] += 1

            dists = sums / (np.ones(self.num_classes) * self.num_classifiers)
            c = dists.argmax()

            if c == self.class_dictionary.get(y[i], -1):
                correct += 1

            results[0][i + 1] = self.class_dictionary.get(y[i], -1)
            results[1][i + 1] = c

            for n in range(self.num_classes):
                results[2 + n][i + 1] = dists[n]

        results[0][0] = correct / num_inst
        return results


class BOSSIndividual:
    """ Single Bag of SFA Symbols (BOSS) classifier

    Bag of SFA Symbols Ensemble: implementation of BOSS from Schaffer :
    @article
    """

    def __init__(self, window_size, word_length, alphabet_size, norm):
        self.window_size = window_size
        self.word_length = word_length
        self.alphabet_size = alphabet_size
        self.norm = norm

        self.transform = SFA(word_length, alphabet_size, window_size=window_size, norm=norm, remove_repeat_words=True,
                             save_words=True)
        self.transformed_data = []
        self.class_vals = []
        self.accuracy = 0

    def fit(self, X, y):
        sfa = self.transform.fit_transform(X)
        self.transformed_data = [series.to_dict() for series in sfa.iloc[:, 0]]
        self.class_vals = y

    def predict(self, X):
        num_insts = X.shape[0]
        classes = np.zeros(num_insts, dtype=np.int_)

        test_bags = self.transform.transform(X)
        test_bags = [series.to_dict() for series in test_bags.iloc[:, 0]]

        for i, test_bag in enumerate(test_bags):
            bestDist = sys.float_info.max
            nn = -1

            for n, bag in enumerate(self.transformed_data):
                dist = boss_distance(test_bag, bag, bestDist)

                if dist < bestDist:
                    bestDist = dist
                    nn = self.class_vals[n]

            classes[i] = nn

        return classes

    def train_predict(self, train_num):
        test_bag = self.transformed_data[train_num]
        best_dist = sys.float_info.max
        nn = -1

        for n, bag in enumerate(self.transformed_data):
            if n == train_num:
                continue

            dist = boss_distance(test_bag, bag, best_dist)

            if dist < best_dist:
                best_dist = dist
                nn = self.class_vals[n]

        return nn

    def shorten_bags(self, word_len):
        newBOSS = BOSSIndividual(self.window_size, word_len, self.alphabet_size, self.norm)
        newBOSS.transform = self.transform
        sfa = self.transform.shorten_bags(word_len)
        newBOSS.transformed_data = [series.to_dict() for series in sfa.iloc[:, 0]]
        newBOSS.class_vals = self.class_vals

        return newBOSS

    def clean(self):
        self.transform.words = None
        self.transform.save_words = False

    def set_word_len(self, word_len):
        self.word_length = word_len
        self.transform.word_length = word_len
