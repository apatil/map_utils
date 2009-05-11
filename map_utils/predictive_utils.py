import pymc as pm
import tables as tb
import numpy as np
from exportAscii import asc_to_ndarray, get_header, exportAscii
from scipy import ndimage, mgrid
from histogram_utils import *
from pymc_utils import predictive_mean_and_std

__all__ = ['grid_convert','mean_reduce','var_reduce','invlogit','hdf5_to_samps','vec_to_asc','asc_to_locs','display_asc','display_datapoints','histogram_reduce','histogram_finalize','maybe_convert','sample_reduce','sample_finalize']

def maybe_convert(ra, field, dtype):
    """
    Tries to cast given field of given record array to given dtype. 
    Raises helpful error on failure.
    """
    arr = ra[field]
    try:
        return arr.astype(dtype)
    except:
        for i in xrange(len(arr)):
            try:
                np.array(arr[i],dtype=dtype)
            except:
                raise ValueError, 'Input column %s, element %i (starting from zero) is %s,\n which cannot be cast to %s'%(field,i,arr[i],dtype)

def validate_format_str(st):
    for i in [0,2]:
        if not st[i] in ['x','y']:
            raise ValueError, 'Directions must be x or y'
    for j in [1,3]:
        if not st[j] in ['-', '+']:
            raise ValueError, 'Orders must be + or -'
            
    if st[0]==st[2]:
        raise ValueError, 'Directions must be different'
    
    
def grid_convert(g, frm, to, validate=False):
    """Converts a grid to a new layout.
      - g : 2d array
      - frm : format string
      - to : format string
      
      Example format strings:
        - x+y+ (the way Anand does it) means that 
            - g[i+1,j] is west of g[i,j]
            - g[i,j+1] is north of g[i,j]
        - y-x+ (map view) means that 
            - g[i+1,j] is south of g[i,j]
            - g[i,j+1] is west of g[i,j]"""
    
    # Validate format strings
    if validate:
        for st in [frm, to]:
            validate_format_str(st)
        
    # Transpose if necessary
    if not frm[0]==to[0]:
        g = g.T
                
    first_dir = to[1]
    if not first_dir == frm[frm.find(to[0])+1]:
        g=g[::-1,:]
        
    sec_dir = to[3]
    if not sec_dir == frm[frm.find(to[2])+1]:
        g=g[:,::-1]
        
    # print first_dir, sec_dir
    return g

def buffer(arr, n=5):
    """Creates an n-pixel buffer in all directions."""
    out = arr.copy()
    if n==0:
        return out
    for i in xrange(1,n+1):
        out[i:,:] += arr[:-i,:]
        out[:-i,:] += arr[i:,:]
        out[:,i:] += arr[:,:-i]
        out[:,:-i] += arr[:,i:]
    return out

def asc_to_locs(fname, path='', thin=1, bufsize=1):
    """Converts an ascii grid to a list of locations where prediction is desired."""
    lon,lat,data = asc_to_ndarray(fname,path)
    data = grid_convert(data,'y-x+','x+y+')
    unmasked = buffer(True-data[::thin,::thin].mask, n=bufsize)
    
    # unmasked = None
    lat,lon = [dir[unmasked] for dir in np.meshgrid(lat[::thin],lon[::thin])]
    # lon,lat = [dir.ravel() for dir in np.meshgrid(lon[::thin],lat[::thin])]
    return np.vstack((lon,lat)).T*np.pi/180., unmasked
    
def display_asc(fname, path='', radians=True, *args, **kwargs):
    """Displays an ascii file as a pylab image."""
    from pylab import imshow
    lon,lat,data = asc_to_ndarray(fname,path)
    if radians:
        lon *= np.pi/180.
        lat *= np.pi/180.
    imshow(grid_convert(data,'y-x+','y+x+'),extent=[lon.min(),lon.max(),lat.min(),lat.max()],*args,**kwargs)
    
def display_datapoints(h5file, path='', cmap=None, *args, **kwargs):
    """Adds as hdf5 archive's logp-mesh to an image."""
    hf = tb.openFile(path+h5file)
    lpm = hf.root.metadata.logp_mesh[:]
    hf.close()
    from pylab import plot
    if cmap is None:
        plot(lpm[:,0],lpm[:,1],*args,**kwargs)
        
def mean_reduce(sofar, next):
    """A function to be used with hdf5_to_samps"""
    if sofar is None:
        return next
    else:
        return sofar + next
        
def var_reduce(sofar, next):
    """A function to be used with hdf5_to_samps"""
    if sofar is None:
        return next**2
    else:
        return sofar + next**2
        
def moments_finalize(prod, n):
    """Finalizes accumulated moments in human-interpreted surfaces."""
    mean = prod[mean_reduce] / n
    var = prod[var_reduce] / n - mean**2
    std = np.sqrt(var)
    std_to_mean = std/mean
    out = {'mean': mean, 'var': var, 'std': std, 'std-to-mean':std_to_mean}
    return out
        
def sample_reduce(sofar, next):
    """A function to be used with hdf5_to_samps. Keeps all samples with no data loss."""
    if sofar is None:
        return [next]
    else:
        sofar.append(next)
        return sofar
        
def sample_finalize(prod, n):
    """Converts accumulated samples to an array for output."""
    return np.array(prod[sample_reduce])

def histogram_reduce(bins, binfn):
    """Produces an accumulator to be used with hdf5_to_samps"""
    def hr(sofar, next):
        if sofar is None:
            sofar = np.zeros(next.shape+(len(bins),), dtype=int, order='F')
        # Call to Fortran function multiinc
        ind = binfn(next)
        # print 'multiinc called, number is %i'%np.sum(sofar)
        multiinc(sofar,ind)
        # print 'Done, number is %i'%np.sum(sofar)
        # print
        return sofar
    return hr
        
def histogram_finalize(bins, q, hr):
    """Converts accumulated histogram raster to desired quantiles"""
    def fin(products, n, bins=bins, q=q, hr=hr):
        out = {}
        hist = products[hr]
        # Call to Fortran function qextract
        quantile_surfs = qextract(hist,n,q,bins)
        for i in xrange(len(q)):
            out['quantile_%s'%q[i]] = quantile_surfs[i]
        # from IPython.Debugger import Pdb
        # Pdb(color_scheme='Linux').set_trace()   
        return out
    return fin

def invlogit(x):
    """A shape-preserving version of PyMC's invlogit."""
    return pm.invlogit(x.ravel()).reshape(x.shape)

# TODO: Use predictive_mean_and_variance
def hdf5_to_samps(chain, metadata, x, burn, thin, total, fns, f_label, f_has_nugget, x_label, pred_cv_dict=None, nugget_label=None, postproc=None, finalize=None):
    """
    Parameters:
        chain : PyTables node
            The chain from which predictions should be made.
        x : array
            The lon, lat locations at which predictions are desired.
        burn : int
            Burnin iterations to discard.
        thin : int
            Number of iterations between ones that get used in the predictions.
        total : int
            Total number of iterations to use in thinning.
        fns : list of functions
            Each function should take four arguments: sofar, next, cols and i.
            Sofar may be None.
            The functions will be applied according to the reduce pattern.
        f_label : string
            The name of the hdf5 node containing f
        f_has_nugget : boolean
            Whether f is nuggeted.
        x_label : string
            The name of the hdf5 node containing the input mesh associated with f
            in the metadata.
        pred_cv_dict : dictionary
            {name : value on x}
        nugget_label : string (optional)
            The name of the hdf5 node giving the nugget variance
        postproc : function (optional)
            This function is applied to the realization before it is passed to
            the fns.
        finalize : function (optional)
            This function is applied to the products before returning. It should
            take a second argument which is the actual number of realizations
            produced.
        """
    
    products = dict(zip(fns, [None]*len(fns)))
    iter = np.arange(burn,len(chain.PyMCsamples),thin)
    n_per = total/len(iter)+1
    actual_total = n_per * len(iter)
    
    cols = chain.PyMCsamples.cols
    x_obs = getattr(metadata,x_label)[:]
    
    # Avoid memory errors
    max_chunksize = 1.e8 / x_obs.shape[0]
    n_chunks = int(x.shape[0]/max_chunksize+1)
    splits = np.array(np.linspace(0,x.shape[0],n_chunks+1)[1:-1],dtype='int')
    x_chunks = np.split(x,splits)
    i_chunks = np.split(np.arange(x.shape[0]), splits)
    
    M_pred = np.empty(x.shape[0])
    V_pred = np.empty(x.shape[0])
    
    for i in iter:
        print i
        
        M_pred, S_pred = predictive_mean_and_std(chain, metadata, i, f_label, x_label, x, f_has_nugget, pred_cv_dict, nugget_label)        
        
        # Postprocess if necessary: logit, etc.
        norms = np.random.normal(size=n_per)
        for j in xrange(n_per):
            surf = M_pred + S_pred * norms[j]
            if postproc is not None:
                surf = postproc(surf)
                        
            # Reduction step
            for f in fns:
                products[f] = f(products[f], surf)
    
    if finalize is not None:
        return finalize(products, actual_total)
    else:          
        return products
        
def normalize_for_mapcoords(arr, max):
    "Used to create inputs to ndimage.map_coordinates."
    arr /= arr.max()
    arr *= max
    
def vec_to_asc(vec, fname, out_fname, unmasked, path=''):
    """
    Converts a vector of outputs on a thin, unmasked, ravelled subset of an
    ascii grid to an ascii file matching the original grid.
    """
    header, f = get_header(fname,path)
    f.close()
    lon,lat,data = asc_to_ndarray(fname,path)
    data = grid_convert(data,'y-x+','x+y+')
    data_thin = np.empty(unmasked.shape)
    data_thin[unmasked] = vec
    
    mapgrid = np.array(mgrid[0:data.shape[0],0:data.shape[1]], dtype=float)
    normalize_for_mapcoords(mapgrid[0], data_thin.shape[0]-1)
    normalize_for_mapcoords(mapgrid[1], data_thin.shape[1]-1)
    
    if data_thin.shape == data.shape:
        out = data
    else:
        out = np.ma.masked_array(ndimage.map_coordinates(data_thin, mapgrid), mask=data.mask)
    
    # import pylab as pl
    # pl.close('all')
    # pl.figure()
    # pl.imshow(out, interpolation='nearest', vmin=0.)
    # pl.colorbar()
    # pl.title('Resampled')
    # 
    # pl.figure()
    # pl.imshow(np.ma.masked_array(data_thin, mask=True-unmasked), interpolation='nearest', vmin=0)
    # pl.colorbar()
    # pl.title('Original')
    
    out_conv = grid_convert(out,'x+y+','y-x+')
    
    header['NODATA_value'] = -9999
    exportAscii(out_conv.data,out_fname,header,True-out_conv.mask)
    
    return out
    
    
if __name__ == '__main__':
    from pylab import *
    import matplotlib
    x, unmasked = asc_to_locs('frame3_10k.asc.txt',thin=20, bufsize=3)    
    # matplotlib.interactive('False')
    # plot(x[:,0],x[:,1],'k.',markersize=1)
    # show()
    hf = tb.openFile('ibd_loc_all_030509.csv.hdf5')
    ch = hf.root.chain0
    meta=hf.root.metadata
    
    def finalize(prod, n):
        mean = prod[mean_reduce] / n
        var = prod[var_reduce] / n - mean**2
        std = np.sqrt(var)
        std_to_mean = std/mean
        return {'mean': mean, 'var': var, 'std': std, 'std-to-mean':std_to_mean}
    
    products = hdf5_to_samps(ch,meta,x,1000,400,5000,[mean_reduce, var_reduce], 'V', invlogit, finalize)

    mean_surf = vec_to_asc(products['mean'],'frame3_10k.asc.txt','ihd-mean.asc',unmasked)
    std_surf = vec_to_asc(products['std-to-mean'],'frame3_10k.asc.txt','ihd-std-to-mean.asc',unmasked)
    